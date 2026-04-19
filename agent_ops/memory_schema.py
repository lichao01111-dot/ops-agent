from agent_kernel.memory import MemorySchema
from agent_ops.schemas import AgentIdentity, MemoryLayer


OPS_MEMORY_SCHEMA = MemorySchema(
    write_permissions={
        AgentIdentity.SYSTEM: {
            MemoryLayer.FACTS,
            MemoryLayer.OBSERVATIONS,
            MemoryLayer.HYPOTHESES,
            MemoryLayer.PLANS,
            MemoryLayer.EXECUTION,
            MemoryLayer.VERIFICATION,
        },
        AgentIdentity.ROUTER: set(),
        AgentIdentity.KNOWLEDGE: {MemoryLayer.FACTS},
        AgentIdentity.READ_OPS: {MemoryLayer.OBSERVATIONS},
        AgentIdentity.DIAGNOSIS: {MemoryLayer.HYPOTHESES},
        AgentIdentity.CHANGE_PLANNER: {MemoryLayer.PLANS},
        AgentIdentity.CHANGE_EXECUTOR: {MemoryLayer.EXECUTION},
        AgentIdentity.VERIFICATION: {MemoryLayer.VERIFICATION},
    }
)
