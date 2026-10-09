import OpenAI from "openai";
if (!process.env.BROWSER_GATEWAY_API_KEY) throw new Error("BROWSER_GATEWAY_API_KEY is required");
const client = new OpenAI({
  apiKey: process.env.BROWSER_GATEWAY_API_KEY,
  baseURL: process.env.OPENAI_BASE_URL ?? "http://127.0.0.1:8091/v1",
  maxRetries: 0, timeout: 190000,
});
const response = await client.chat.completions.create({
  model: "browser-auto", messages: [{role: "user", content: "Say hello"}],
});
console.log(response.choices[0]?.message.content);
