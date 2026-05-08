"""
Example AGCL plugin.

Drops a /node/hello route and a `hello` CLI command into the running
node + TUI. Use as a template for your own plugins. See docs/endpoint.md
for the full plugin contract.
"""


def register(ctx):

    @ctx.router.get("/hello")
    def hello():
        return {"hello": "from example plugin", "loaded": True}

    @ctx.add_command("hello", help="say hi from a plugin")
    def _cmd_hello(args: str) -> None:
        target = args.strip() or "world"
        print(f"hello, {target}!")
