from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import google.generativeai as genai
from dotenv import load_dotenv
import os
import uvicorn
import re
import time
import json
import uuid

# Load environment variables
load_dotenv()
API_key = os.getenv("API_KEY")
if API_key:
    API_key = API_key.strip().strip('"').strip("'")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip().strip('"').strip("'")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite").strip().strip('"').strip("'")
try:
    MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "2"))
except ValueError as exc:
    raise ValueError("GEMINI_MAX_RETRIES must be an integer in .env") from exc
SESSION_MAX_ITEMS = int(os.getenv("SESSION_MAX_ITEMS", "30"))

def _validate_api_key(value: str | None) -> str:
    if not value:
        raise ValueError("API_KEY is not set in .env file.")

    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("API_KEY is empty in .env file.")

    placeholders = {"your_api_key_here", "replace_me", "changeme", "api_key"}
    if cleaned.lower() in placeholders:
        raise ValueError("API_KEY looks like a placeholder. Put a real Gemini API key in .env.")

    # Gemini keys are typically prefixed with AIza and have no spaces.
    if " " in cleaned or len(cleaned) < 20:
        raise ValueError("API_KEY format looks invalid. Check .env and paste the full Gemini API key.")

    return cleaned

API_key = _validate_api_key(API_key)

# Initialize FastAPI app
app = FastAPI()

# Configure CORS
 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ai-code-generator-tau.vercel.app"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure GenAI
genai.configure(api_key=API_key)
primary_model = genai.GenerativeModel(MODEL_NAME)
fallback_model = genai.GenerativeModel(FALLBACK_MODEL)
SESSION_MEMORY: dict[str, dict] = {}

# Pydantic model for request
class PromptRequest(BaseModel):
    prompt: str
    allow_external_libs: bool = False
    include_summary: bool = True
    session_id: str | None = None
    chat_history: list[dict] | None = None

class PromptEditRequest(BaseModel):
    edit_instruction: str
    session_id: str | None = None
    current_code: str | None = None
    allow_external_libs: bool = False
    include_summary: bool = True
    chat_history: list[dict] | None = None

ALLOWED_ZERO_DEPENDENCY_PACKAGES = {"react", "react-dom"}
IMPORT_PATTERN = re.compile(
    r"""(?mx)
    ^\s*import\s+[^'"]*['"](?P<path1>[^'"]+)['"]\s*;?
    |
    require\(\s*['"](?P<path2>[^'"]+)['"]\s*\)
    """
)

JSON_OUTPUT_CONTRACT = """
Return STRICT JSON only with this exact structure:
{
  "title": "short title",
  "summary": "1-2 line summary",
  "code": "full React component code"
}
No markdown. No backticks.
"""

COMPONENT_NAME_PATTERNS = [
    re.compile(r"\bfunction\s+([A-Z][A-Za-z0-9_]*)\s*\("),
    re.compile(r"\bconst\s+([A-Z][A-Za-z0-9_]*)\s*=\s*\("),
    re.compile(r"\bconst\s+([A-Z][A-Za-z0-9_]*)\s*=\s*async\s*\("),
    re.compile(r"\bconst\s+([A-Z][A-Za-z0-9_]*)\s*=\s*memo\s*\("),
    re.compile(r"\bconst\s+([A-Z][A-Za-z0-9_]*)\s*=\s*forwardRef\s*\("),
]

def _base_package(dep: str) -> str:
    if dep.startswith("@"):
        parts = dep.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else dep
    return dep.split("/")[0]

def _extract_dependencies(code: str) -> list[str]:
    deps: list[str] = []
    for match in IMPORT_PATTERN.finditer(code):
        dep = match.group("path1") or match.group("path2")
        if not dep:
            continue
        # Ignore local/absolute URLs and file-relative imports.
        if dep.startswith(".") or dep.startswith("/") or dep.startswith("http://") or dep.startswith("https://"):
            continue
        pkg = _base_package(dep)
        if pkg not in deps:
            deps.append(pkg)
    return deps

def _build_system_context(allow_external_libs: bool, include_summary: bool) -> str:
    deps_rule = (
        "Do not import any external library other than 'react' and 'react-dom'. "
        "Use plain JSX and inline CSS only."
        if not allow_external_libs
        else "External libraries are allowed, but only when truly necessary. Keep dependencies minimal."
    )
    summary_rule = "Include a concise summary in JSON." if include_summary else "Set summary to an empty string."
    return (
        "You are a strict React code generator.\n"
        "Generate exactly one functional component.\n"
        "The component must be default-exported.\n"
        "Use React 18+ style imports only.\n"
        "No explanations, no comments, no multiple examples.\n"
        f"{deps_rule}\n"
        f"{summary_rule}\n"
        f"{JSON_OUTPUT_CONTRACT}"
    )

def _build_stream_system_context(allow_external_libs: bool) -> str:
    deps_rule = (
        "Do not import any external library other than 'react' and 'react-dom'. "
        "Use plain JSX and inline CSS only."
        if not allow_external_libs
        else "External libraries are allowed, but only when truly necessary. Keep dependencies minimal."
    )
    return (
        "You are a strict React code generator.\n"
        "Generate exactly one functional component.\n"
        "The component must be default-exported.\n"
        "Use React 18+ style imports only.\n"
        "No explanations, no comments, no markdown, no backticks.\n"
        f"{deps_rule}\n"
        "Return only the final component code."
    )

def _get_or_create_session(session_id: str | None) -> tuple[str, dict]:
    sid = (session_id or "").strip() or str(uuid.uuid4())
    if sid not in SESSION_MEMORY:
        SESSION_MEMORY[sid] = {"history": [], "versions": [], "latest_code": ""}
    return sid, SESSION_MEMORY[sid]

def _trim_items(items: list, max_items: int = SESSION_MAX_ITEMS) -> list:
    if len(items) <= max_items:
        return items
    return items[-max_items:]

def _history_to_text(history: list[dict]) -> str:
    lines: list[str] = []
    for item in _trim_items(history):
        role = str(item.get("role", "user")).strip() or "user"
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)

def _save_session_version(session: dict, payload: dict) -> int:
    session["versions"].append(payload)
    session["versions"] = _trim_items(session["versions"])
    session["latest_code"] = payload.get("code", "")
    return len(session["versions"])

def _detect_component_name(code: str) -> str | None:
    for pattern in COMPONENT_NAME_PATTERNS:
        match = pattern.search(code)
        if match:
            return match.group(1)
    return None

def _ensure_default_export(code: str) -> str:
    if re.search(r"\bexport\s+default\b", code):
        return code

    name = _detect_component_name(code)
    if not name:
        return code

    return f"{code.rstrip()}\n\nexport default {name};\n"

def _clean_generated_code(raw_code: str) -> str:
    if not raw_code:
        return ""
    cleaned = raw_code.replace("```jsx", "").replace("```javascript", "").replace("```", "").strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    return cleaned.strip()

def _safe_json_extract(text: str) -> dict:
    # Accept raw JSON or JSON surrounded by accidental text.
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("Model output is not valid JSON.")

def _generate_with_retry(final_prompt: str) -> tuple[dict, str]:
    errors: list[str] = []

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = primary_model.generate_content(final_prompt)
            data = _safe_json_extract(response.text)
            return data, MODEL_NAME
        except Exception as exc:
            errors.append(f"primary attempt {attempt + 1}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.2 * (attempt + 1))

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = fallback_model.generate_content(final_prompt)
            data = _safe_json_extract(response.text)
            return data, FALLBACK_MODEL
        except Exception as exc:
            errors.append(f"fallback attempt {attempt + 1}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.2 * (attempt + 1))

    combined = " | ".join(errors)
    if "reported as leaked" in combined.lower():
        raise HTTPException(
            status_code=403,
            detail="Gemini API key is blocked as leaked. Generate a new API key and update API_KEY in .env.",
        )
    raise HTTPException(status_code=502, detail=f"Gemini request failed after retries: {combined}")

def _stream_with_fallback(prompt: str) -> tuple[object, str]:
    try:
        return primary_model.generate_content(prompt, stream=True), MODEL_NAME
    except Exception as primary_exc:
        try:
            return fallback_model.generate_content(prompt, stream=True), FALLBACK_MODEL
        except Exception as fallback_exc:
            combined = f"primary stream: {primary_exc} | fallback stream: {fallback_exc}"
            if "reported as leaked" in combined.lower():
                raise HTTPException(
                    status_code=403,
                    detail="Gemini API key is blocked as leaked. Generate a new API key and update API_KEY in .env.",
                )
            raise HTTPException(status_code=502, detail=f"Gemini streaming failed: {combined}")

def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

@app.post("/prompt")
async def get_prompt(prompt_request: PromptRequest):
    system_context = _build_system_context(prompt_request.allow_external_libs, prompt_request.include_summary)
    sid, session = _get_or_create_session(prompt_request.session_id)
    incoming_history = prompt_request.chat_history or []
    merged_history = _trim_items(session.get("history", []) + incoming_history)
    history_block = _history_to_text(merged_history)
    final_prompt = (
        f"{system_context}\n\n"
        f"Conversation history:\n{history_block}\n\n"
        f"User request:\n{prompt_request.prompt}"
    )
    data, used_model = _generate_with_retry(final_prompt)

    code = str(data.get("code", "")).strip()
    title = str(data.get("title", "Generated Component")).strip() or "Generated Component"
    summary = str(data.get("summary", "")).strip() if prompt_request.include_summary else ""

    if not code:
        raise HTTPException(status_code=502, detail="Model returned empty code.")

    code = _ensure_default_export(code)

    deps = _extract_dependencies(code)
    disallowed = [d for d in deps if d not in ALLOWED_ZERO_DEPENDENCY_PACKAGES]

    if disallowed and not prompt_request.allow_external_libs:
        result = {
            "success": False,
            "error_type": "dependency_violation",
            "message": "Generated code requires external dependencies. Regenerate with allow_external_libs=true or simplify prompt.",
            "title": title,
            "summary": summary,
            "code": code,
            "dependencies": deps,
            "missing_dependencies": disallowed,
            "mode": "zero_dependency",
            "model": used_model,
            "session_id": sid,
        }
        session["history"] = _trim_items(merged_history + [{"role": "user", "content": prompt_request.prompt}])
        return result

    version_no = _save_session_version(
        session,
        {
            "title": title,
            "summary": summary,
            "code": code,
            "dependencies": deps,
            "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
            "model": used_model,
        },
    )
    session["history"] = _trim_items(
        merged_history
        + [{"role": "user", "content": prompt_request.prompt}, {"role": "assistant", "content": summary or title}]
    )

    return {
        "success": True,
        "title": title,
        "summary": summary,
        "code": code,
        "dependencies": deps,
        "missing_dependencies": [],
        "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
        "model": used_model,
        "session_id": sid,
        "version": version_no,
    }

@app.post("/prompt/stream")
async def get_prompt_stream(prompt_request: PromptRequest):
    system_context = _build_stream_system_context(prompt_request.allow_external_libs)
    sid, session = _get_or_create_session(prompt_request.session_id)
    incoming_history = prompt_request.chat_history or []
    merged_history = _trim_items(session.get("history", []) + incoming_history)
    history_block = _history_to_text(merged_history)
    final_prompt = (
        f"{system_context}\n\n"
        f"Conversation history:\n{history_block}\n\n"
        f"User request:\n{prompt_request.prompt}"
    )

    def event_stream():
        yield _sse("status", {"message": "starting", "session_id": sid})
        stream, used_model = _stream_with_fallback(final_prompt)
        raw_chunks: list[str] = []

        try:
            for chunk in stream:
                text = getattr(chunk, "text", "") or ""
                if not text:
                    continue
                raw_chunks.append(text)
                yield _sse("chunk", {"text": text})

            raw_code = "".join(raw_chunks)
            code = _ensure_default_export(_clean_generated_code(raw_code))
            if not code:
                yield _sse("error", {"message": "Model returned empty code."})
                return

            deps = _extract_dependencies(code)
            disallowed = [d for d in deps if d not in ALLOWED_ZERO_DEPENDENCY_PACKAGES]
            title = "Generated Component"
            summary = ""

            if disallowed and not prompt_request.allow_external_libs:
                session["history"] = _trim_items(merged_history + [{"role": "user", "content": prompt_request.prompt}])
                yield _sse(
                    "done",
                    {
                        "success": False,
                        "error_type": "dependency_violation",
                        "message": "Generated code requires external dependencies. Regenerate with allow_external_libs=true or simplify prompt.",
                        "title": title,
                        "summary": summary,
                        "code": code,
                        "dependencies": deps,
                        "missing_dependencies": disallowed,
                        "mode": "zero_dependency",
                        "model": used_model,
                        "session_id": sid,
                    },
                )
                return

            version_no = _save_session_version(
                session,
                {
                    "title": title,
                    "summary": summary,
                    "code": code,
                    "dependencies": deps,
                    "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
                    "model": used_model,
                },
            )
            session["history"] = _trim_items(
                merged_history
                + [{"role": "user", "content": prompt_request.prompt}, {"role": "assistant", "content": title}]
            )

            yield _sse(
                "done",
                {
                    "success": True,
                    "title": title,
                    "summary": summary,
                    "code": code,
                    "dependencies": deps,
                    "missing_dependencies": [],
                    "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
                    "model": used_model,
                    "session_id": sid,
                    "version": version_no,
                },
            )
        except HTTPException as exc:
            yield _sse("error", {"message": str(exc.detail), "status_code": exc.status_code})
        except Exception as exc:
            msg = str(exc)
            if "reported as leaked" in msg.lower():
                msg = "Gemini API key is blocked as leaked. Generate a new API key and update API_KEY in .env."
            yield _sse("error", {"message": msg})

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/prompt/edit")
async def edit_prompt(prompt_request: PromptEditRequest):
    system_context = _build_system_context(prompt_request.allow_external_libs, prompt_request.include_summary)
    sid, session = _get_or_create_session(prompt_request.session_id)

    base_code = (prompt_request.current_code or session.get("latest_code", "")).strip()
    if not base_code:
        raise HTTPException(status_code=400, detail="No base code found. Send current_code or generate first.")

    incoming_history = prompt_request.chat_history or []
    merged_history = _trim_items(session.get("history", []) + incoming_history)
    history_block = _history_to_text(merged_history)

    edit_prompt_text = (
        f"{system_context}\n\n"
        "You are editing existing React code.\n"
        "Preserve current behavior unless the instruction asks to change it.\n"
        "Return complete updated code in JSON.\n\n"
        f"Conversation history:\n{history_block}\n\n"
        f"Edit instruction:\n{prompt_request.edit_instruction}\n\n"
        f"Current code:\n{base_code}"
    )

    data, used_model = _generate_with_retry(edit_prompt_text)
    code = str(data.get("code", "")).strip()
    title = str(data.get("title", "Updated Component")).strip() or "Updated Component"
    summary = str(data.get("summary", "")).strip() if prompt_request.include_summary else ""

    if not code:
        raise HTTPException(status_code=502, detail="Model returned empty code.")

    code = _ensure_default_export(code)
    deps = _extract_dependencies(code)
    disallowed = [d for d in deps if d not in ALLOWED_ZERO_DEPENDENCY_PACKAGES]

    if disallowed and not prompt_request.allow_external_libs:
        return {
            "success": False,
            "error_type": "dependency_violation",
            "message": "Updated code requires external dependencies. Retry with allow_external_libs=true or adjust instruction.",
            "title": title,
            "summary": summary,
            "code": code,
            "dependencies": deps,
            "missing_dependencies": disallowed,
            "mode": "zero_dependency",
            "model": used_model,
            "session_id": sid,
        }

    version_no = _save_session_version(
        session,
        {
            "title": title,
            "summary": summary,
            "code": code,
            "dependencies": deps,
            "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
            "model": used_model,
            "edit_instruction": prompt_request.edit_instruction,
        },
    )
    session["history"] = _trim_items(
        merged_history
        + [
            {"role": "user", "content": prompt_request.edit_instruction},
            {"role": "assistant", "content": summary or title},
        ]
    )

    return {
        "success": True,
        "title": title,
        "summary": summary,
        "code": code,
        "dependencies": deps,
        "missing_dependencies": [],
        "mode": "external_allowed" if prompt_request.allow_external_libs else "zero_dependency",
        "model": used_model,
        "session_id": sid,
        "version": version_no,
    }

# Run the app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8010)
