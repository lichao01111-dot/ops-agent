"""
Jenkins Pipeline Tool
- 根据项目类型生成 Jenkinsfile
- 查询 Build 状态
- 分析构建失败原因
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog
from langchain_core.tools import tool

from config import settings

logger = structlog.get_logger()

# ===== Jenkins API Client =====

class JenkinsClient:
    """Jenkins REST API 封装"""

    def __init__(self):
        self.base_url = settings.jenkins_url.rstrip("/")
        self.auth = (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None

    async def _request(self, method: str, path: str, **kwargs) -> dict | str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}{path}"
            resp = await client.request(method, url, auth=self.auth, **kwargs)
            resp.raise_for_status()
            if "application/json" in resp.headers.get("content-type", ""):
                return resp.json()
            return resp.text

    async def get_job_info(self, job_name: str) -> dict:
        return await self._request("GET", f"/job/{job_name}/api/json")

    async def get_last_build(self, job_name: str) -> dict:
        return await self._request("GET", f"/job/{job_name}/lastBuild/api/json")

    async def get_build_log(self, job_name: str, build_number: int) -> str:
        return await self._request("GET", f"/job/{job_name}/{build_number}/consoleText")

    async def get_build_info(self, job_name: str, build_number: int) -> dict:
        return await self._request("GET", f"/job/{job_name}/{build_number}/api/json")

    async def create_job(self, job_name: str, config_xml: str) -> str:
        return await self._request(
            "POST",
            f"/createItem?name={job_name}",
            content=config_xml,
            headers={"Content-Type": "application/xml"},
        )

    async def trigger_build(self, job_name: str, params: dict | None = None) -> str:
        path = f"/job/{job_name}/buildWithParameters" if params else f"/job/{job_name}/build"
        return await self._request("POST", path, params=params)


jenkins_client = JenkinsClient()


# ===== Jenkinsfile Templates =====

PIPELINE_TEMPLATES = {
    "java_maven": """pipeline {{
    agent any
    
    environment {{
        PROJECT_NAME = '{project_name}'
        REGISTRY = '{registry}'
        IMAGE_TAG = "${{BUILD_NUMBER}}"
    }}
    
    stages {{
        stage('Checkout') {{
            steps {{
                git branch: '{branch}', url: '{repo_url}'
            }}
        }}
        
        stage('Build') {{
            steps {{
                sh 'mvn clean package -DskipTests'
            }}
        }}
        
        stage('Unit Test') {{
            steps {{
                sh 'mvn test'
            }}
            post {{
                always {{
                    junit '**/target/surefire-reports/*.xml'
                }}
            }}
        }}
        
        stage('SonarQube Analysis') {{
            when {{ branch 'main' }}
            steps {{
                sh 'mvn sonar:sonar'
            }}
        }}
        
        stage('Docker Build & Push') {{
            steps {{
                sh "docker build -t ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} ."
                sh "docker push ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}}"
            }}
        }}
        
        stage('Deploy to {deploy_env}') {{
            steps {{
                sh "kubectl set image deployment/${{PROJECT_NAME}} ${{PROJECT_NAME}}=${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} -n {namespace}"
            }}
        }}
    }}
    
    post {{
        failure {{
            // 通知相关人员
            echo "Build failed for ${{PROJECT_NAME}} #${{BUILD_NUMBER}}"
        }}
        success {{
            echo "Build succeeded for ${{PROJECT_NAME}} #${{BUILD_NUMBER}}"
        }}
    }}
}}""",

    "nodejs": """pipeline {{
    agent any
    
    environment {{
        PROJECT_NAME = '{project_name}'
        REGISTRY = '{registry}'
        IMAGE_TAG = "${{BUILD_NUMBER}}"
        NODE_ENV = 'production'
    }}
    
    stages {{
        stage('Checkout') {{
            steps {{
                git branch: '{branch}', url: '{repo_url}'
            }}
        }}
        
        stage('Install Dependencies') {{
            steps {{
                sh 'npm ci'
            }}
        }}
        
        stage('Lint') {{
            steps {{
                sh 'npm run lint'
            }}
        }}
        
        stage('Test') {{
            steps {{
                sh 'npm test'
            }}
        }}
        
        stage('Build') {{
            steps {{
                sh 'npm run build'
            }}
        }}
        
        stage('Docker Build & Push') {{
            steps {{
                sh "docker build -t ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} ."
                sh "docker push ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}}"
            }}
        }}
        
        stage('Deploy to {deploy_env}') {{
            steps {{
                sh "kubectl set image deployment/${{PROJECT_NAME}} ${{PROJECT_NAME}}=${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} -n {namespace}"
            }}
        }}
    }}
}}""",

    "python": """pipeline {{
    agent any
    
    environment {{
        PROJECT_NAME = '{project_name}'
        REGISTRY = '{registry}'
        IMAGE_TAG = "${{BUILD_NUMBER}}"
    }}
    
    stages {{
        stage('Checkout') {{
            steps {{
                git branch: '{branch}', url: '{repo_url}'
            }}
        }}
        
        stage('Setup Python Env') {{
            steps {{
                sh 'python -m venv venv'
                sh '. venv/bin/activate && pip install -r requirements.txt'
            }}
        }}
        
        stage('Lint') {{
            steps {{
                sh '. venv/bin/activate && ruff check .'
            }}
        }}
        
        stage('Test') {{
            steps {{
                sh '. venv/bin/activate && pytest --junitxml=report.xml'
            }}
            post {{
                always {{
                    junit 'report.xml'
                }}
            }}
        }}
        
        stage('Docker Build & Push') {{
            steps {{
                sh "docker build -t ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} ."
                sh "docker push ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}}"
            }}
        }}
        
        stage('Deploy to {deploy_env}') {{
            steps {{
                sh "kubectl set image deployment/${{PROJECT_NAME}} ${{PROJECT_NAME}}=${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} -n {namespace}"
            }}
        }}
    }}
}}""",

    "go": """pipeline {{
    agent any
    
    environment {{
        PROJECT_NAME = '{project_name}'
        REGISTRY = '{registry}'
        IMAGE_TAG = "${{BUILD_NUMBER}}"
        GOPATH = "${{WORKSPACE}}/go"
    }}
    
    stages {{
        stage('Checkout') {{
            steps {{
                git branch: '{branch}', url: '{repo_url}'
            }}
        }}
        
        stage('Build') {{
            steps {{
                sh 'go build -o bin/${{PROJECT_NAME}} ./cmd/...'
            }}
        }}
        
        stage('Test') {{
            steps {{
                sh 'go test ./... -v -coverprofile=coverage.out'
            }}
        }}
        
        stage('Docker Build & Push') {{
            steps {{
                sh "docker build -t ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} ."
                sh "docker push ${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}}"
            }}
        }}
        
        stage('Deploy to {deploy_env}') {{
            steps {{
                sh "kubectl set image deployment/${{PROJECT_NAME}} ${{PROJECT_NAME}}=${{REGISTRY}}/${{PROJECT_NAME}}:${{IMAGE_TAG}} -n {namespace}"
            }}
        }}
    }}
}}""",
}


# ===== LangChain Tools =====

@tool
async def generate_jenkinsfile(
    project_name: str,
    language: str,
    repo_url: str = "https://git.example.com/project.git",
    branch: str = "main",
    registry: str = "registry.example.com",
    deploy_env: str = "staging",
    namespace: str = "default",
) -> str:
    """根据项目类型自动生成 Jenkinsfile。

    Args:
        project_name: 项目名称，如 user-service
        language: 编程语言/构建工具，支持: java_maven, nodejs, python, go
        repo_url: Git 仓库地址
        branch: 分支名称
        registry: Docker 镜像仓库地址
        deploy_env: 部署环境名称
        namespace: K8s namespace
    """
    template_key = language.lower().replace("-", "_").replace(" ", "_")

    # 映射常见别名
    aliases = {
        "java": "java_maven", "maven": "java_maven", "spring": "java_maven",
        "springboot": "java_maven", "spring_boot": "java_maven",
        "node": "nodejs", "javascript": "nodejs", "typescript": "nodejs",
        "react": "nodejs", "vue": "nodejs", "nextjs": "nodejs",
        "python3": "python", "django": "python", "flask": "python", "fastapi": "python",
        "golang": "go",
    }
    template_key = aliases.get(template_key, template_key)

    if template_key not in PIPELINE_TEMPLATES:
        available = list(PIPELINE_TEMPLATES.keys())
        return f"不支持的语言类型: {language}。当前支持: {', '.join(available)}"

    jenkinsfile = PIPELINE_TEMPLATES[template_key].format(
        project_name=project_name,
        repo_url=repo_url,
        branch=branch,
        registry=registry,
        deploy_env=deploy_env,
        namespace=namespace,
    )

    return json.dumps({
        "status": "generated",
        "project_name": project_name,
        "language": template_key,
        "jenkinsfile": jenkinsfile,
        "message": f"已为 {project_name} 生成 {template_key} 类型的 Jenkinsfile。请确认后我可以帮你在 Jenkins 中创建 Job。"
    }, ensure_ascii=False)


@tool
async def query_jenkins_build(
    job_name: str,
    build_number: Optional[int] = None,
) -> str:
    """查询 Jenkins 构建状态和信息。

    Args:
        job_name: Jenkins Job 名称
        build_number: 构建编号，不填则查询最近一次构建
    """
    try:
        if build_number:
            info = await jenkins_client.get_build_info(job_name, build_number)
        else:
            info = await jenkins_client.get_last_build(job_name)

        return json.dumps({
            "job_name": job_name,
            "build_number": info.get("number"),
            "result": info.get("result", "IN_PROGRESS"),
            "duration_ms": info.get("duration", 0),
            "timestamp": info.get("timestamp", 0),
            "building": info.get("building", False),
            "url": info.get("url", ""),
        }, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Jenkins API 错误: {e.response.status_code}", "job_name": job_name})
    except Exception as e:
        return json.dumps({"error": f"查询失败: {str(e)}", "job_name": job_name})


@tool
async def get_jenkins_build_log(
    job_name: str,
    build_number: int,
    tail_lines: int = 100,
) -> str:
    """获取 Jenkins 构建日志，用于分析构建失败原因。

    Args:
        job_name: Jenkins Job 名称
        build_number: 构建编号
        tail_lines: 返回最后多少行日志，默认100行
    """
    try:
        log_text = await jenkins_client.get_build_log(job_name, build_number)
        lines = log_text.strip().split("\n")
        if len(lines) > tail_lines:
            lines = lines[-tail_lines:]

        return json.dumps({
            "job_name": job_name,
            "build_number": build_number,
            "total_lines": len(log_text.strip().split("\n")),
            "returned_lines": len(lines),
            "log": "\n".join(lines),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"获取日志失败: {str(e)}"})


# 导出所有 tools
jenkins_tools = [generate_jenkinsfile, query_jenkins_build, get_jenkins_build_log]
