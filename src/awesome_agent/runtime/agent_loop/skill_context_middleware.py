from __future__ import annotations

from awesome_agent.modeling import ModelRequest, SystemMessage
from awesome_agent.runtime.agent_loop.contracts import MiddlewareContext
from awesome_agent.runtime.token_accounting import TokenAccountant


class SkillContextMiddleware:
    name = "skill_context"

    def __init__(
        self,
        *,
        token_accountant: TokenAccountant | None = None,
        max_context_tokens: int = 800,
    ) -> None:
        self.token_accountant = token_accountant or TokenAccountant()
        self.max_context_tokens = max_context_tokens

    async def before_model_call(
        self,
        request: ModelRequest,
        context: MiddlewareContext,
    ) -> ModelRequest:
        skill_context = self._skill_context(context)
        if not skill_context:
            return request
        return request.model_copy(
            update={
                "messages": [
                    SystemMessage(content=skill_context),
                    *request.messages,
                ]
            }
        )

    def _skill_context(self, context: MiddlewareContext) -> str:
        view = context.skill_runtime_view
        if view is None or not view.skills:
            return ""
        blocks = [
            f"- {skill.id}@{skill.version}\n{skill.instructions.strip()}"
            for skill in view.skills
            if skill.instructions.strip()
        ]
        if not blocks:
            return ""
        content = "Skill context:\n" + "\n\n".join(blocks)
        estimate = self.token_accountant.estimate_text(content).with_margin()
        if estimate.tokens <= self.max_context_tokens:
            return content
        return _truncate_to_token_budget(content, self.max_context_tokens)


def _truncate_to_token_budget(content: str, max_tokens: int) -> str:
    # The default estimator is character based; use a conservative char bound.
    max_chars = max(0, max_tokens * 3)
    return (
        content[:max_chars].rstrip()
        + "\n[Skill context truncated to fit token budget.]"
    )
