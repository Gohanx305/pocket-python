"""FFmpeg (WebAssembly) from Python.

    import ffmpeg
    await ffmpeg.run("-i", "video.mp4", "-vn", "audio.mp3")
    await ffmpeg.run("-i video.mp4 -ss 5 -frames:v 1 thumb.jpg")

Input files are read from the current folder, and every file FFmpeg
writes lands back in the same folder. `-y` is always on.
"""
import shlex
import _bridge
from pyodide.ffi import to_js


class FFmpegError(RuntimeError):
    pass


def _args(args):
    if len(args) == 1 and isinstance(args[0], str):
        return shlex.split(args[0])
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return [str(a) for a in args[0]]
    return [str(a) for a in args]


async def run(*args, quiet=False, check=True):
    """Runs ffmpeg with the given arguments. Returns the list of files it wrote."""
    argv = _args(args)
    if argv and argv[0] == "ffmpeg":
        argv = argv[1:]
    res = (await _bridge.ffmpeg(to_js(argv), bool(quiet))).to_py()
    if check and res["code"] != 0:
        raise FFmpegError(f"ffmpeg exited with code {res['code']}")
    return res["files"]


async def probe(path):
    """Returns the stream/format info FFmpeg prints for a file."""
    res = (await _bridge.ffmpeg(to_js(["-hide_banner", "-i", str(path)]), True)).to_py()
    return res["log"]


async def version():
    res = (await _bridge.ffmpeg(to_js(["-version"]), True)).to_py()
    return res["log"]
