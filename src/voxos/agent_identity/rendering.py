from voxos.agent_identity.models import AgentIdentity


def render_agent_identity_section(identity: AgentIdentity) -> str:
    lines = [
        "# Agent Identity",
        f"- Name: {identity.display_name}",
    ]
    if identity.description is not None:
        lines.append(f"- Description: {identity.description}")
    lines.append(
        "- This saved identity is authoritative. Use this name when asked who "
        "you are, and do not substitute a product or platform name.",
    )
    return "\n".join(lines)
