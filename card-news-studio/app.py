"""Local single-user tutorial. Run one Uvicorn worker only."""
import argparse
import asyncio
import copy
import hmac
import json
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from engines import run_engine
from renderer import render
from storyboard import SAMPLE_SOURCE, demo_story, make_prompt, parse_json, replace_card, validate_story

ROOT = Path(__file__).parent.resolve()
WORKSPACE = Path(os.environ.get("STUDIO_WORKSPACE", ROOT / "workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
JOBS = {}
TASKS = {}


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def directory(pid):
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise HTTPException(404, "Unknown project")
    path = WORKSPACE / pid
    if not (path / "project.json").exists():
        raise HTTPException(404, "Unknown project")
    return path


def load_project(pid):
    return json.loads((directory(pid) / "project.json").read_text(encoding="utf-8"))


@asynccontextmanager
async def lifespan(app):
    yield
    for task in TASKS.values():
        task.cancel()
    await asyncio.gather(*TASKS.values(), return_exceptions=True)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.headers.get("host", "")
    hostname = urlparse("http://" + host).hostname
    token = os.environ.get("STUDIO_TOKEN", "")
    allowed_host = hostname in {"127.0.0.1", "localhost", "::1"} or (token and hostname and hostname.endswith(".ts.net"))
    if not allowed_host:
        return JSONResponse({"detail": "Host not allowed"}, status_code=403)
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("authorization", "")
        if token and not hmac.compare_digest(supplied, "Bearer " + token):
            return JSONResponse({"detail": "Access token required"}, status_code=401)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Origin mismatch"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-studio-request") != "1":
            return JSONResponse({"detail": "Studio request header required"}, status_code=403)
        size = request.headers.get("content-length", "0")
        if not size.isdigit() or int(size) > 40000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'; connect-src 'self'"
    return response


class ProjectInput(BaseModel):
    topic: str = Field(min_length=1, max_length=60)
    audience: str = Field(min_length=1, max_length=40)
    source_text: str = Field(min_length=1, max_length=16000)
    source_title: str = Field(default="사용자가 제공한 자료", min_length=1, max_length=80)
    engine: str = "demo"
    card_count: int = Field(default=4, ge=3, le=8)


class JobInput(BaseModel):
    action: str
    card_id: str | None = None
    instruction: str = Field(default="", max_length=2000)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static/index.html")


@app.get("/api/projects")
async def projects():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(WORKSPACE.glob("*/project.json"))]


@app.post("/api/projects")
async def create(data: ProjectInput):
    if data.engine not in {"demo", "claude", "opencode"}:
        raise HTTPException(400, "Unknown engine")
    if data.engine == "demo" and (data.source_text != SAMPLE_SOURCE or data.card_count != 4):
        raise HTTPException(400, "Demo only supports the provided four-card fixture; choose a real engine for your own source")
    pid = uuid.uuid4().hex[:12]
    path = WORKSPACE / pid
    path.mkdir()
    project = {"id": pid, "topic": data.topic, "audience": data.audience, "sources": [{"id": "s1", "title": data.source_title, "text": data.source_text}], "engine": data.engine, "session_id": None, "card_count": data.card_count}
    save(path / "project.json", project)
    return project


@app.get("/api/sample")
async def sample():
    return {"source_text": SAMPLE_SOURCE}


@app.get("/api/projects/{pid}")
async def project(pid: str):
    path = directory(pid)
    return {"project": load_project(pid), "storyboard": json.loads((path / "storyboard.json").read_text(encoding="utf-8")) if (path / "storyboard.json").exists() else None, "files": sorted(p.name for p in (path / "exports").glob("*") if p.is_file()), "jobs": [j for j in JOBS.values() if j["project_id"] == pid]}


async def execute(job, data):
    path = directory(job["project_id"])
    project = load_project(job["project_id"])
    job["status"] = "running"
    def emit(message):
        job["events"].append(str(message)[:500])
        job["events"] = job["events"][-100:]
    try:
        async with asyncio.timeout(300):
            story = json.loads((path / "storyboard.json").read_text(encoding="utf-8")) if (path / "storyboard.json").exists() else None
            if data.action != "render":
                emit(f"engine={project['engine']} / {'resume' if project['session_id'] else 'new session'}")
                if project["engine"] == "demo":
                    await asyncio.sleep(0.1)
                    if data.action == "revise":
                        card = copy.deepcopy(next(c for c in story["cards"] if c["id"] == data.card_id))
                        card["headline"] = "한 권을 함께 나눠요"
                        story = replace_card(story, card, data.card_id)
                        emit("예제 수정: 요청 의미를 해석하지 않고 정해진 제목을 적용했습니다.")
                    else:
                        story = demo_story(project)
                    session = project["session_id"] or "demo-" + uuid.uuid4().hex
                else:
                    prompt = make_prompt(project, story if data.action == "revise" else None, data.card_id, data.instruction)
                    skill = ROOT / "skills/card-news-editor/SKILL.md"
                    if skill.exists():
                        prompt = skill.read_text(encoding="utf-8") + "\n\n" + prompt
                    reference = ROOT / "skills/card-news-editor/references/editorial-decisions.md"
                    if reference.exists():
                        prompt = reference.read_text(encoding="utf-8") + "\n\n" + prompt
                    text, session = await run_engine(project["engine"], prompt, project["session_id"], path, emit)
                    # Persist the actual engine session even if editorial validation fails.
                    project["session_id"] = session
                    save(path / "project.json", project)
                    parsed = parse_json(text)
                    story = replace_card(story, parsed, data.card_id) if data.action == "revise" else validate_story(parsed, project)
                validate_story(story, project)
                project["session_id"] = session
                save(path / "project.json", project)
                save(path / "storyboard.json", story)
                # Old exports no longer represent the current storyboard.
                shutil.rmtree(path / "exports", ignore_errors=True)
                emit("원고 검증 및 저장 완료")
            if story is None:
                raise ValueError("Generate a storyboard first")
            staging = path / "rendering"
            shutil.rmtree(staging, ignore_errors=True)
            emit("Chromium으로 PNG/PDF 렌더링")
            files = await render(story, staging)
            shutil.rmtree(path / "exports", ignore_errors=True)
            staging.rename(path / "exports")
            emit(f"출력 완료: {len(files)} files")
            job["status"] = "succeeded"
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        emit("실행 취소 완료")
    except Exception as exc:
        job["status"] = "failed"
        emit(f"{type(exc).__name__}: {exc}")
    finally:
        save(path / "last-job.json", job)


@app.post("/api/projects/{pid}/jobs")
async def start(pid: str, data: JobInput):
    path = directory(pid)
    if data.action not in {"generate", "revise", "render"}:
        raise HTTPException(400, "Unknown action")
    if data.action == "generate" and (path / "storyboard.json").exists():
        raise HTTPException(409, "A storyboard already exists; revise it or create a new project")
    if any(j["project_id"] == pid and j["status"] in {"queued", "running", "cancelling"} for j in JOBS.values()):
        raise HTTPException(409, "A job is already active in this project")
    if data.action in {"revise", "render"} and not (path / "storyboard.json").exists():
        raise HTTPException(400, "Generate a storyboard first")
    if data.action == "revise":
        story = json.loads((path / "storyboard.json").read_text(encoding="utf-8"))
        if data.card_id not in [c["id"] for c in story["cards"]] or not data.instruction.strip():
            raise HTTPException(400, "Select a card and enter a revision")
    jid = uuid.uuid4().hex
    job = {"id": jid, "project_id": pid, "status": "queued", "events": []}
    JOBS[jid] = job
    TASKS[jid] = asyncio.create_task(execute(job, data))
    return job


@app.get("/api/jobs/{jid}")
async def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[jid]


@app.post("/api/jobs/{jid}/cancel")
async def cancel(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Unknown job")
    if JOBS[jid]["status"] in {"running", "queued"}:
        JOBS[jid]["status"] = "cancelling"
        TASKS[jid].cancel()
        await asyncio.gather(TASKS[jid], return_exceptions=True)
        if JOBS[jid]["status"] == "cancelling":
            JOBS[jid]["status"] = "cancelled"
    return JOBS[jid]


@app.get("/api/projects/{pid}/files/{name}")
async def download(pid: str, name: str):
    if not re.fullmatch(r"c[1-8]\.png|cards\.(pdf|html)|storyboard\.json", name):
        raise HTTPException(404, "Unknown export")
    path = directory(pid) / "exports" / name
    if not path.is_file():
        raise HTTPException(404, "Export unavailable")
    return FileResponse(path, filename=name)


async def run_demo():
    p = await create(ProjectInput(topic="동네 도서 교환", audience="이웃 주민", source_text=SAMPLE_SOURCE, source_title="수업용 가상 행사 규칙", engine="demo"))
    job = await start(p["id"], JobInput(action="generate"))
    await TASKS[job["id"]]
    print(json.dumps(job, ensure_ascii=False, indent=2))
    print(WORKSPACE / p["id"] / "exports")
    if job["status"] != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        asyncio.run(run_demo())
    else:
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("STUDIO_TOKEN"):
            parser.error("Remote binding requires STUDIO_TOKEN; use loopback with Tailscale Serve")
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
