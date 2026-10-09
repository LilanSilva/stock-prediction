"""Generic client example; run in an existing environment with langchain-openai installed."""

import os
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel


class Classification(BaseModel):
    event_type: Literal["OTHER", "EARNINGS"]


client = ChatOpenAI(
    base_url=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8091/v1"),
    api_key=os.environ["BROWSER_GATEWAY_API_KEY"],
    model="browser-auto",
    timeout=210,
    max_retries=0,
    use_responses_api=False,
)
print(client.invoke("Reply with exactly: gateway works").content)
for method in ("json_schema", "function_calling"):
    structured = client.with_structured_output(Classification, method=method)
    print(method, structured.invoke("An example company announced its quarterly earnings."))
