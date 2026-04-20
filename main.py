#!/usr/bin/env python3
"""
OpsAgent 启动入口
Usage:
    python main.py                  # 启动 API 服务
    python main.py --index ./docs   # 索引文档到知识库
"""
import argparse
import asyncio
import sys

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = structlog.get_logger()


def run_server():
    """启动 API 服务"""
    import uvicorn
    from config import settings

    logger.info(
        "starting_ops_agent_server",
        host=settings.server_host,
        port=settings.server_port,
        llm_provider=settings.llm_provider.value,
        llm_model=settings.llm_model,
    )

    uvicorn.run(
        "gateway.app:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_level="info",
    )


async def index_docs(docs_dir: str):
    """索引文档到知识库"""
    from tools.knowledge_tool import knowledge_base

    logger.info("indexing_documents", directory=docs_dir)
    report = knowledge_base.ingest_directory(docs_dir)
    stats = knowledge_base.get_stats()
    logger.info(
        "indexing_complete",
        documents=report["document_count"],
        chunks=report["chunk_count"],
        total_documents=stats["document_count"],
        total_chunks=stats["chunk_count"],
    )


async def interactive_chat():
    """交互式命令行对话（调试用）"""
    from agent_kernel.schemas import ChatRequest
    from agent_ops import create_ops_agent

    agent = create_ops_agent()
    session_id = "cli-debug"
    print("\n🤖 OpsAgent 交互模式 (输入 'quit' 退出)\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ("quit", "exit", "q"):
                break
            if not user_input:
                continue

            request = ChatRequest(
                message=user_input,
                session_id=session_id,
                user_id="cli-user",
            )
            response = await agent.chat(request)

            print(f"\n🤖 OpsAgent: {response.message}")
            if response.tool_calls:
                print(f"   📎 Tools used: {', '.join(tc.tool_name for tc in response.tool_calls)}")
            print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")

    print("Bye! 👋")


def main():
    parser = argparse.ArgumentParser(description="OpsAgent - DevOps AI 智能运维助手")
    parser.add_argument("--serve", action="store_true", default=True, help="启动 API 服务 (默认)")
    parser.add_argument("--index", type=str, metavar="DIR", help="索引指定目录的文档到知识库")
    parser.add_argument("--chat", action="store_true", help="交互式命令行对话 (调试)")

    args = parser.parse_args()

    if args.index:
        asyncio.run(index_docs(args.index))
    elif args.chat:
        asyncio.run(interactive_chat())
    else:
        run_server()


if __name__ == "__main__":
    main()
