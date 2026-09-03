"""Self-contained peer-read benchmark with symmetric TCP latency."""

import argparse
import asyncio
import os
import pathlib
import shutil
import signal
import ssl
import subprocess
import tempfile
import time

import blosc2
import httpx
import numpy as np
import peer_read
import single_fetch

from caterva2.tests.test_peers import _start, _unused_tcp_ports


def wait_for_cli(cli: str, api_url: str, process: subprocess.Popen) -> None:
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError(f"Toxiproxy exited during startup with status {process.returncode}")
        result = subprocess.run(
            [cli, "--host", api_url, "list"], capture_output=True, check=False, text=True
        )
        if result.returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("Toxiproxy API did not start")


def toxiproxy(cli: str, api_url: str, *args: str) -> None:
    subprocess.run([cli, "--host", api_url, *args], check=True, capture_output=True, text=True)


async def run(args: argparse.Namespace) -> None:
    caddy = shutil.which("caddy")
    toxiproxy_server = shutil.which("toxiproxy-server")
    toxiproxy_cli = shutil.which("toxiproxy-cli")
    missing = [name for name, path in (("caddy", caddy), ("toxiproxy", toxiproxy_server)) if not path]
    if missing:
        raise RuntimeError(f"missing benchmark tools: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="caterva2-http2-latency-") as tmp:
        tmpdir = pathlib.Path(tmp)
        server_port, caddy_port, delayed_port, toxiproxy_port = _unused_tcp_ports(4)
        server_dir = tmpdir / "server"
        public = server_dir / "public"
        public.mkdir(parents=True)
        data = np.random.default_rng(42).random((args.chunks, args.items_per_chunk))
        blosc2.asarray(
            data,
            chunks=(1, args.items_per_chunk),
            blocks=(1, args.items_per_chunk),
            urlpath=str(public / "latency.b2nd"),
        )

        server = _start(server_dir, server_port)
        caddy_env = dict(
            os.environ,
            CATERVA2_UPSTREAM=f"127.0.0.1:{server_port}",
            CATERVA2_H2_ADDRESS=f"localhost:{caddy_port}",
            XDG_DATA_HOME=str(tmpdir / "caddy-data"),
            XDG_CONFIG_HOME=str(tmpdir / "caddy-config"),
        )
        caddyfile = pathlib.Path(__file__).with_name("Caddyfile")
        caddy_process = subprocess.Popen(
            [caddy, "run", "--config", str(caddyfile)],
            env=caddy_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        toxiproxy_process = subprocess.Popen(
            [toxiproxy_server, "-host", "127.0.0.1", "-port", str(toxiproxy_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            api_url = f"http://127.0.0.1:{toxiproxy_port}"
            wait_for_cli(toxiproxy_cli, api_url, toxiproxy_process)
            one_way_ms = args.rtt_ms / 2
            name = "peer-tls"
            toxiproxy(
                toxiproxy_cli,
                api_url,
                "create",
                "--listen",
                f"127.0.0.1:{delayed_port}",
                "--upstream",
                f"127.0.0.1:{caddy_port}",
                name,
            )
            for direction in ("downstream", "upstream"):
                direction_flag = "--downstream" if direction == "downstream" else "--upstream"
                toxiproxy(
                    toxiproxy_cli,
                    api_url,
                    "toxic",
                    "add",
                    "--type",
                    "latency",
                    "--attribute",
                    f"latency={one_way_ms:g}",
                    direction_flag,
                    name,
                )

            root_ca = tmpdir / "caddy-data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"
            for _ in range(50):
                if caddy_process.poll() is not None:
                    raise RuntimeError(f"Caddy exited during startup with status {caddy_process.returncode}")
                if root_ca.exists():
                    try:
                        context = ssl.create_default_context(cafile=str(root_ca))
                        with httpx.Client(http2=True, verify=context, timeout=1) as client:
                            ready = client.get(f"https://localhost:{caddy_port}/api/roots")
                        if ready.is_success and ready.http_version == "HTTP/2":
                            break
                    except httpx.TransportError:
                        pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Caddy verified HTTP/2 endpoint did not start")

            os.environ["SSL_CERT_FILE"] = str(root_ca)
            print(
                f"dataset_chunks={args.chunks} chunk_bytes={args.items_per_chunk * 8} "
                f"simulated_rtt_ms={args.rtt_ms:g}"
            )
            delayed_url = f"https://localhost:{delayed_port}"
            if args.workload == "peer-read":
                await peer_read.main(
                    argparse.Namespace(
                        # Same TLS endpoint and proxy path. Only HTTPX's protocol
                        # offer differs between the two benchmark arms.
                        http1_url=delayed_url,
                        http2_url=delayed_url,
                        path="@public/latency.b2nd",
                        concurrency=args.concurrency,
                        repeat=args.repeat,
                    )
                )
            else:
                single_fetch.main(
                    argparse.Namespace(
                        urlbase=delayed_url,
                        path="@public/latency.b2nd",
                        slice=args.slice,
                        repeat=args.repeat,
                        timeout=30,
                        pause=args.pause,
                    )
                )
        finally:
            for process in (toxiproxy_process, caddy_process, server):
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("peer-read", "single-fetch"), default="peer-read")
    parser.add_argument("--rtt-ms", type=float, default=50)
    parser.add_argument("--chunks", type=int, default=64)
    parser.add_argument("--items-per-chunk", type=int, default=65536)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--slice", default="5:9", help="slice used by the single-fetch workload")
    parser.add_argument("--pause", type=float, default=0.25)
    args = parser.parse_args()
    if min(args.rtt_ms, args.chunks, args.items_per_chunk, args.concurrency, args.repeat) <= 0:
        parser.error("all numeric arguments must be positive")
    if args.pause < 0:
        parser.error("--pause cannot be negative")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
