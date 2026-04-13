from tools.jenkins_tool import jenkins_tools
from tools.k8s_tool import k8s_tools
from tools.log_tool import log_tools
from tools.knowledge_tool import knowledge_tools

# 所有可用工具的注册表
ALL_TOOLS = jenkins_tools + k8s_tools + log_tools + knowledge_tools

__all__ = [
    "ALL_TOOLS",
    "jenkins_tools",
    "k8s_tools",
    "log_tools",
    "knowledge_tools",
]
