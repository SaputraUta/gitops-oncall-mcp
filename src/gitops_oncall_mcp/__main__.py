"""CLI entrypoint — `python -m gitops_oncall_mcp` or `gitops-oncall-mcp`."""

from .server import main

if __name__ == "__main__":
    main()
