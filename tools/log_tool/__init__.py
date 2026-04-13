"""
日志分析 Tool
- 对接多种日志系统（ES / Loki / SLS）
- 日志搜索与聚合
- LLM 驱动的根因分析
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional

import httpx
import structlog
from langchain_core.tools import tool

from config import settings, LogProvider

logger = structlog.get_logger()


# ===== Log Provider 接口 =====

class BaseLogProvider(ABC):
    """日志系统适配器抽象基类"""

    @abstractmethod
    async def search(
        self,
        service: str,
        time_range_minutes: int = 60,
        level: str = "ERROR",
        keyword: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """搜索日志"""
        ...

    @abstractmethod
    async def get_error_stats(
        self,
        service: str,
        time_range_minutes: int = 60,
    ) -> dict:
        """获取错误统计（按 Exception 类型聚合）"""
        ...


class ElasticsearchLogProvider(BaseLogProvider):
    """Elasticsearch / ELK 日志适配器"""

    def __init__(self):
        self.base_url = settings.elasticsearch_url.rstrip("/")

    async def search(
        self,
        service: str,
        time_range_minutes: int = 60,
        level: str = "ERROR",
        keyword: str = "",
        limit: int = 50,
    ) -> list[dict]:
        now = datetime.utcnow()
        start = now - timedelta(minutes=time_range_minutes)

        query = {
            "size": limit,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": [
                        {"match": {"service": service}},
                        {"match": {"level": level}},
                        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": now.isoformat()}}},
                    ]
                }
            },
        }
        if keyword:
            query["query"]["bool"]["must"].append({"match_phrase": {"message": keyword}})

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{self.base_url}/{service}-*/_search", json=query)
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                return [
                    {
                        "timestamp": h["_source"].get("@timestamp", ""),
                        "level": h["_source"].get("level", ""),
                        "message": h["_source"].get("message", "")[:1000],  # 截断长消息
                        "logger": h["_source"].get("logger", ""),
                        "thread": h["_source"].get("thread", ""),
                        "stack_trace": h["_source"].get("stack_trace", "")[:2000],
                    }
                    for h in hits
                ]
        except Exception as e:
            logger.error("elasticsearch_search_failed", error=str(e), service=service)
            return [{"error": f"Elasticsearch 查询失败: {str(e)}"}]

    async def get_error_stats(self, service: str, time_range_minutes: int = 60) -> dict:
        now = datetime.utcnow()
        start = now - timedelta(minutes=time_range_minutes)

        query = {
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        {"match": {"service": service}},
                        {"match": {"level": "ERROR"}},
                        {"range": {"@timestamp": {"gte": start.isoformat()}}},
                    ]
                }
            },
            "aggs": {
                "error_types": {
                    "terms": {"field": "exception_class.keyword", "size": 20}
                }
            },
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{self.base_url}/{service}-*/_search", json=query)
                resp.raise_for_status()
                data = resp.json()
                total = data.get("hits", {}).get("total", {}).get("value", 0)
                buckets = data.get("aggregations", {}).get("error_types", {}).get("buckets", [])
                return {
                    "service": service,
                    "time_range_minutes": time_range_minutes,
                    "total_errors": total,
                    "error_types": [{"type": b["key"], "count": b["doc_count"]} for b in buckets],
                }
        except Exception as e:
            return {"error": f"统计查询失败: {str(e)}"}


class LokiLogProvider(BaseLogProvider):
    """Grafana Loki 日志适配器"""

    def __init__(self):
        self.base_url = settings.loki_url.rstrip("/")

    async def search(
        self,
        service: str,
        time_range_minutes: int = 60,
        level: str = "ERROR",
        keyword: str = "",
        limit: int = 50,
    ) -> list[dict]:
        now = datetime.utcnow()
        start = now - timedelta(minutes=time_range_minutes)

        log_ql = f'{{service="{service}"}} |= "{level}"'
        if keyword:
            log_ql += f' |= "{keyword}"'

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/loki/api/v1/query_range",
                    params={
                        "query": log_ql,
                        "start": int(start.timestamp() * 1e9),
                        "end": int(now.timestamp() * 1e9),
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                for stream in data.get("data", {}).get("result", []):
                    for ts, line in stream.get("values", []):
                        results.append({
                            "timestamp": datetime.fromtimestamp(int(ts) / 1e9).isoformat(),
                            "message": line[:1000],
                            "labels": stream.get("stream", {}),
                        })
                return results
        except Exception as e:
            return [{"error": f"Loki 查询失败: {str(e)}"}]

    async def get_error_stats(self, service: str, time_range_minutes: int = 60) -> dict:
        # Loki 的聚合能力有限，返回基础信息
        logs = await self.search(service, time_range_minutes, "ERROR", "", 200)
        return {
            "service": service,
            "time_range_minutes": time_range_minutes,
            "total_errors": len(logs),
            "note": "Loki 不支持细粒度聚合，建议配合 Grafana 查看详情",
        }


def _get_log_provider() -> BaseLogProvider:
    """根据配置获取日志适配器"""
    if settings.log_provider == LogProvider.ELASTICSEARCH:
        return ElasticsearchLogProvider()
    elif settings.log_provider == LogProvider.LOKI:
        return LokiLogProvider()
    else:
        # Fallback to ES
        return ElasticsearchLogProvider()


# ===== LangChain Tools =====

@tool
async def search_logs(
    service: str,
    time_range_minutes: int = 60,
    level: str = "ERROR",
    keyword: str = "",
    limit: int = 50,
) -> str:
    """搜索服务日志。

    Args:
        service: 服务名称，如 user-service, order-service
        time_range_minutes: 搜索最近多少分钟的日志，默认60
        level: 日志级别过滤：ERROR, WARN, INFO, DEBUG
        keyword: 关键词过滤
        limit: 最大返回条数
    """
    provider = _get_log_provider()
    logs = await provider.search(
        service=service,
        time_range_minutes=time_range_minutes,
        level=level,
        keyword=keyword,
        limit=min(limit, 100),
    )

    return json.dumps({
        "service": service,
        "level": level,
        "time_range_minutes": time_range_minutes,
        "keyword": keyword or "(none)",
        "count": len(logs),
        "logs": logs,
    }, ensure_ascii=False)


@tool
async def get_error_statistics(
    service: str,
    time_range_minutes: int = 60,
) -> str:
    """获取服务错误统计，按异常类型聚合。

    Args:
        service: 服务名称
        time_range_minutes: 统计最近多少分钟
    """
    provider = _get_log_provider()
    stats = await provider.get_error_stats(service, time_range_minutes)
    return json.dumps(stats, ensure_ascii=False)


log_tools = [search_logs, get_error_statistics]
