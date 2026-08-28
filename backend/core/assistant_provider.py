from __future__ import annotations

"""
Assistant provider interface (a "port", same idea as VisionProvider).

The assistant only ever talks to this interface, never to Gemini or any
other model API directly. That is what lets the provider be swapped -
for a cheaper one, a faster one, or one that runs on the shop's own
server when the rest of the stack moves there - without the snapshot
builder or the API layer changing a line.

There is one method, and it deliberately takes the shop's figures as a
dict rather than as prose the caller has already written. The provider's
job is to explain numbers, not to find them: everything it may say has
been worked out before it is called (see core/assistant.build_snapshot),
so the model is never in the position of having to do arithmetic to
answer, which is where invented figures come from.

WHAT LEAVES THE SHOP.

Every call sends this snapshot to whoever implements the port, which for
the first implementation means Google. That is takings, ingredient cost,
profit and best-selling dishes for one branch and one period. It is not
customer data and not staff data, and the owner has accepted it - but it
is worth stating here rather than in a commit message, because the next
person to add a field to the snapshot is deciding what leaves the shop
and should know that is what they are doing.

Deliberately NOT in the snapshot, ever:
  - anything identifying a customer
  - staff names, roles or pay
  - the tenant id, the branch's Loyverse token, or any other credential
  - raw receipts (a bill at a time is a person's order, and the
    assistant's questions are all about days and months anyway)
"""

from abc import ABC, abstractmethod


class AssistantProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    def ask(self, instructions: str, snapshot: dict, question: str) -> str:
        """Answer one question about one shop's figures.

        `instructions` is how to behave, `snapshot` is everything it is
        allowed to know, `question` is what was asked. Returns plain text
        for a person to read - not JSON, not markdown tables: the answer
        is shown in a chat bubble on a phone.

        Raises AssistantError on any failure, so the caller can say so
        plainly instead of showing an empty bubble.
        """


    def converse(self, instructions: str, snapshot: dict, question: str,
                 tools: list[dict], run_tool) -> str:
        """Answer, with the option of asking for data first.

        `tools` describes what may be asked for; `run_tool(name, args)`
        runs it and returns a dict. A provider that implements this lets
        the model choose WHICH figures it needs - it still never produces
        one, because run_tool is Python and the model only writes the
        request (see core/shop_query.py).

        Not abstract, and it falls through to `ask` rather than raising:
        a provider without function calling should still answer from the
        figures it was handed, less completely rather than not at all.
        """
        return self.ask(instructions, snapshot, question)


class AssistantError(Exception):
    """Raised when a provider can't produce a usable answer - no API key,
    quota hit, network failure, or an empty response.

    The message is shown to the shop owner, so it is in Thai and says
    what to do rather than what broke.
    """
