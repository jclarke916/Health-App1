"""Make an Ollama that is bound to one address also answer on 127.0.0.1.

    pythonw tools/ollama_localhost_forward.py <ollama-ip> [port]

Why: `tailscale serve` can only proxy to localhost, but this box runs Ollama
bound to its Tailscale address only (OLLAMA_HOST=<tailscale-ip>:11434), which
keeps it off the LAN. This is a dumb TCP pipe 127.0.0.1:<port> -> <ip>:<port>,
so streaming responses pass through untouched and Ollama's exposure does not
change. Exits quietly if something already listens on 127.0.0.1:<port>.
"""
import asyncio
import sys


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main(target, port):
    async def handle(local_r, local_w):
        try:
            remote_r, remote_w = await asyncio.open_connection(target, port)
        except OSError:
            local_w.close()
            return
        await asyncio.gather(pipe(local_r, remote_w), pipe(remote_r, local_w))

    try:
        server = await asyncio.start_server(handle, '127.0.0.1', port)
    except OSError:
        return  # already running, or Ollama itself now listens on localhost
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 11434))
