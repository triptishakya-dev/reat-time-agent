"""Windows device discovery and experimental Codex voice client."""

import argparse
import base64
import itertools
import json
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path


RATE = 24000
REALTIME = "thread/realtime/"


def voice(args):
    import sounddevice as audio

    def endpoint(index, direction):
        device = audio.query_devices(index, direction)
        if "rb meta" not in device["name"].lower() and "ray-ban" not in device["name"].lower():
            if not args.allow_other_audio:
                raise ValueError(f"{device['name']} is not a Ray-Ban endpoint")
        print(f"{direction}: {device['name']}", flush=True)

    endpoint(args.input, "input")
    endpoint(args.output, "output")
    audio.check_input_settings(device=args.input, channels=1, dtype="int16", samplerate=RATE)
    audio.check_output_settings(device=args.output, channels=1, dtype="int16", samplerate=RATE)
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is missing from PATH")

    with subprocess.Popen(
        [executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    ) as server:
        incoming = queue.Queue()
        ids = itertools.count(1)

        def read_messages():
            try:
                for line in server.stdout:
                    incoming.put(json.loads(line))
            finally:
                incoming.put(None)

        threading.Thread(target=read_messages, daemon=True).start()

        def send(method, params=None, request=True):
            message = {"method": method}
            if params is not None:
                message["params"] = params
            if request:
                message["id"] = next(ids)
            server.stdin.write(json.dumps(message) + "\n")
            server.stdin.flush()
            return message.get("id")

        def receive(timeout):
            message = incoming.get(timeout=timeout)
            if message is None:
                raise RuntimeError("Codex app-server exited")
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            if "id" in message and "method" in message:
                reply = {"id": message["id"], "error": {
                    "code": -32601, "message": "Use the Codex desktop app for interactive approvals",
                }}
                server.stdin.write(json.dumps(reply) + "\n")
                server.stdin.flush()
            return message

        def call(method, params):
            request_id = send(method, params)
            while True:
                message = receive(30)
                if message.get("id") == request_id and "result" in message:
                    return message["result"]

        microphone = queue.Queue(maxsize=25)

        def capture(data, frames, timing, status):
            try:
                microphone.put_nowait(bytes(data))
            except queue.Full:
                pass

        try:
            call("initialize", {
                "clientInfo": {"name": "ray_ban_voice", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            send("initialized", request=False)
            thread = call("thread/start", {"cwd": str(Path.cwd())})["thread"]
            scope = {"threadId": thread["id"]}
            send(REALTIME + "start", {
                **scope, "outputModality": "audio", "voice": "sol", "version": "v3",
                "transport": {"type": "websocket"},
            })
            while True:
                message = receive(30)
                method = message.get("method")
                if method in {REALTIME + "error", REALTIME + "closed"}:
                    raise RuntimeError(str(message["params"]))
                if method == REALTIME + "started":
                    break

            print("Microphone streaming to Codex. Press Ctrl+C to stop.", flush=True)
            with audio.RawInputStream(
                device=args.input, samplerate=RATE, channels=1, dtype="int16",
                blocksize=960, callback=capture,
            ), audio.RawOutputStream(
                device=args.output, samplerate=RATE, channels=1, dtype="int16",
            ) as speaker:
                while True:
                    try:
                        chunk = microphone.get(timeout=0.02)
                    except queue.Empty:
                        pass
                    else:
                        send(REALTIME + "appendAudio", {**scope, "audio": {
                            "data": base64.b64encode(chunk).decode("ascii"),
                            "sampleRate": RATE, "numChannels": 1,
                            "samplesPerChannel": len(chunk) // 2,
                        }})
                    while True:
                        try:
                            event = receive(0)
                        except queue.Empty:
                            break
                        method = event.get("method")
                        if method == REALTIME + "outputAudio/delta":
                            chunk = event["params"]["audio"]
                            if (chunk["sampleRate"], chunk["numChannels"]) != (RATE, 1):
                                raise RuntimeError("Unexpected Codex playback audio format")
                            speaker.write(base64.b64decode(chunk["data"], validate=True))
                        elif method in {REALTIME + "error", REALTIME + "closed"}:
                            raise RuntimeError(str(event["params"]))
        except KeyboardInterrupt:
            if "scope" in locals():
                send(REALTIME + "stop", scope)
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe", help="Show Windows Bluetooth, audio and camera devices")
    commands.add_parser("audio-devices", help="List Python audio endpoint indices")
    live = commands.add_parser("voice", help="Run a Codex voice session using Sol")
    live.add_argument("--input", type=int, required=True)
    live.add_argument("--output", type=int, required=True)
    live.add_argument("--allow-other-audio", action="store_true")
    args = parser.parse_args()
    if args.command == "probe":
        for category in ("Bluetooth", "AudioEndpoint", "Camera"):
            print(f"\n{category}", flush=True)
            subprocess.run(["pnputil", "/enum-devices", "/connected", "/class", category], check=True)
    elif args.command == "audio-devices":
        import sounddevice

        print(sounddevice.query_devices())
    else:
        voice(args)


if __name__ == "__main__":
    try:
        main()
    except queue.Empty:
        sys.exit("Timed out waiting for Codex app-server")
    except Exception as error:
        sys.exit(f"{type(error).__name__}: {error}")
