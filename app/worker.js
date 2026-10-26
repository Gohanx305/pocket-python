// Python runs here, off the main thread, so the page never freezes.
import { loadPyodide } from "../vendor/pyodide/pyodide.mjs";

const HOME = "/home/pyodide";
const WORK = HOME + "/files";
const LIB = HOME + "/lib";
let py = null;
let mupdfReady = null;
let persistent = false;
let nextId = 1;
const pending = new Map();

const post = (msg, transfer) => self.postMessage(msg, transfer || []);

// Batch console output so a print() loop doesn't flood the page.
let outBuf = [];
let outTimer = null;
function out(kind, text) {
  outBuf.push({ kind, text });
  if (!outTimer) outTimer = setTimeout(flushOut, 40);
}
function flushOut() {
  clearTimeout(outTimer);
  outTimer = null;
  if (outBuf.length) post({ type: "out", items: outBuf });
  outBuf = [];
}

function syncfs(populate) {
  if (!persistent) return Promise.resolve();
  return new Promise((res) => py.FS.syncfs(populate, (err) => res(err)));
}

function walk(dir, prefix = "") {
  const FS = py.FS;
  const list = [];
  for (const name of FS.readdir(dir)) {
    if (name === "." || name === "..") continue;
    const full = dir + "/" + name;
    const st = FS.stat(full);
    const rel = prefix + name;
    if (FS.isDir(st.mode)) list.push({ name: rel, dir: true }, ...walk(full, rel + "/"));
    else list.push({ name: rel, size: st.size, mtime: st.mtime.getTime() });
  }
  return list;
}

function writeFile(rel, data) {
  const full = WORK + "/" + rel;
  const dir = full.slice(0, full.lastIndexOf("/"));
  py.FS.mkdirTree(dir);
  py.FS.writeFile(full, data);
}

function exists(rel) {
  try {
    return py.FS.isFile(py.FS.stat(py.FS.cwd() + "/" + rel).mode);
  } catch {
    return false;
  }
}

function ask(msg, transfer) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    post({ ...msg, id }, transfer);
  });
}

const bridge = {
  async ffmpeg(argsProxy, quiet) {
    flushOut();
    const args = Array.from(argsProxy, String);

    const inputs = {};
    const transfer = [];
    const cwd = py.FS.cwd();
    for (const a of args) {
      if (a.startsWith("-") || inputs[a] || a.startsWith("/")) continue;
      if (exists(a)) {
        const data = py.FS.readFile(cwd + "/" + a);
        inputs[a] = data;
        transfer.push(data.buffer);
      }
    }
    const res = await ask({ type: "ffmpeg", args, inputs, quiet }, transfer);
    const names = [];
    for (const [name, data] of Object.entries(res.files || {})) {
      const full = cwd + "/" + name;
      py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
      py.FS.writeFile(full, data);
      names.push(name);
    }
    return { code: res.code, files: names, log: res.log || "" };
  },
  show(path, caption) {
    flushOut();
    const full = path.startsWith("/") ? path : py.FS.cwd() + "/" + path;
    const data = py.FS.readFile(full);
    post({ type: "show", name: path.split("/").pop(), caption: caption || "", data }, [data.buffer]);
  },
};

const SHIMS = {
  "pymupdf.py": "from fitz import *\nfrom fitz import open, __version__\n",
  "_sitecustom.py": `
import builtins, _bridge
def show(path, caption=""):
    """Shows an image, audio, video or PDF from the files folder in the output."""
    _bridge.show(str(path), str(caption))
def _no_input(prompt=""):
    raise RuntimeError("input() is not available here. Put the values in variables at the top of the script.")
builtins.show = show
builtins.input = _no_input
`,
};

async function init() {
  const base = new URL("../vendor/pyodide/", import.meta.url).href;
  post({ type: "status", text: "تحميل بايثون…", step: 1 });
  py = await loadPyodide({
    indexURL: base,
    stdLibURL: base + "python_stdlib.zip.wasm",
    stdout: (t) => out("out", t),
    stderr: (t) => out("err", t),
  });
  py.setStdout({ batched: (t) => out("out", t) });
  py.setStderr({ batched: (t) => out("err", t) });

  const FS = py.FS;
  FS.mkdirTree(WORK);
  FS.mkdirTree(LIB);
  try {
    FS.mount(py.FS.filesystems.IDBFS, {}, WORK);
    persistent = true;
    const err = await syncfs(true);
    if (err) persistent = false;
  } catch {
    persistent = false;
  }

  py.registerJsModule("_bridge", bridge);
  for (const name of ["fitz.py", "ffmpeg.py"]) {
    const src = await (await fetch(new URL("./" + name, import.meta.url), { cache: "no-cache" })).text();
    FS.writeFile(LIB + "/" + name, src);
  }
  for (const [name, src] of Object.entries(SHIMS)) FS.writeFile(LIB + "/" + name, src);
  py.runPython(`import sys; sys.path.insert(0, "${LIB}"); import _sitecustom`);
  FS.chdir(WORK);

  post({ type: "ready", version: py.runPython("import sys; sys.version.split()[0]"), persistent, files: walk(WORK) });
  mupdfReady = import("../vendor/mupdf/mupdf.js").then((m) => {
    py.registerJsModule("mupdf", { ...m });
    post({ type: "lib", name: "mupdf" });
    return m;
  });
  mupdfReady.catch((e) => post({ type: "lib", name: "mupdf", error: String(e) }));
}

function cleanTrace(msg) {
  const lines = String(msg).split("\n");
  const keep = [];
  let skip = false;
  for (const l of lines) {
    if (/^\s*File "\/lib\/python3\d+\.zip\/_pyodide/.test(l)) {
      skip = true;
      continue;
    }
    if (skip && /^\s{4}/.test(l)) continue;
    skip = false;
    keep.push(l);
  }
  return keep.join("\n").trim();
}

async function run(code) {
  if (/\b(fitz|pymupdf|mupdf)\b/.test(code)) {
    post({ type: "status", text: "تحميل MuPDF…" });
    await mupdfReady;
  }
  post({ type: "status", text: "يعمل…" });
  py.FS.chdir(WORK);
  const globals = py.globals.get("dict")();
  globals.set("__name__", "__main__");
  const t0 = performance.now();
  let error = null;
  try {
    await py.runPythonAsync(code, { globals, filename: "main.py" });
  } catch (e) {
    error = cleanTrace(e.message);
  } finally {
    globals.destroy();
  }
  flushOut();
  await syncfs(false);
  post({ type: "done", error, ms: Math.round(performance.now() - t0), files: walk(WORK) });
}

self.onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.type === "init") await init();
    else if (m.type === "run") await run(m.code);
    else if (m.type === "reply") {
      pending.get(m.id)?.(m);
      pending.delete(m.id);
    } else if (m.type === "put") {
      for (const f of m.files) writeFile(f.name, new Uint8Array(f.data));
      await syncfs(false);
      post({ type: "files", files: walk(WORK) });
    } else if (m.type === "pdfpreview") {
      const mu = await mupdfReady;
      const doc = mu.Document.openDocument(py.FS.readFile(WORK + "/" + m.name), "application/pdf");
      const count = doc.countPages();
      const pages = [];
      for (let i = 0; i < Math.min(count, 4); i++) {
        const page = doc.loadPage(i);
        const [x0, , x1] = page.getBounds();
        const s = Math.min(3, 900 / Math.max(1, x1 - x0));
        const png = page.toPixmap([s, 0, 0, s, 0, 0], mu.ColorSpace.DeviceRGB, false, true).asPNG();
        pages.push(png.slice());
      }
      post({ type: "pdfpages", req: m.req, count, pages }, pages.map((p) => p.buffer));
    } else if (m.type === "get") {
      const data = py.FS.readFile(WORK + "/" + m.name);
      post({ type: "file", req: m.req, name: m.name, data }, [data.buffer]);
    } else if (m.type === "delete") {
      const full = WORK + "/" + m.name;
      if (py.FS.isDir(py.FS.stat(full).mode)) {
        const rm = (p) => {
          for (const n of py.FS.readdir(p)) {
            if (n === "." || n === "..") continue;
            const c = p + "/" + n;
            py.FS.isDir(py.FS.stat(c).mode) ? rm(c) : py.FS.unlink(c);
          }
          py.FS.rmdir(p);
        };
        rm(full);
      } else py.FS.unlink(full);
      await syncfs(false);
      post({ type: "files", files: walk(WORK) });
    } else if (m.type === "rename") {
      py.FS.rename(WORK + "/" + m.from, WORK + "/" + m.to);
      await syncfs(false);
      post({ type: "files", files: walk(WORK) });
    }
  } catch (err) {
    flushOut();
    post({ type: "fatal", text: String(err && err.message ? err.message : err), during: m.type });
  }
};
