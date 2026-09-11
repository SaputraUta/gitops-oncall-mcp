"""GitHub adapter (REST v3, fine-grained PAT)."""

from __future__ import annotations

import base64

import httpx

from ..config import GitHubConfig
from .base import Commit, PipelineResult, PRResult, TagInfo
from .util import sort_tags_newest_first


class GitHubAdapter:
    def __init__(self, cfg: GitHubConfig):
        self._cfg = cfg
        self._owner_repo = f"{cfg.owner}/{cfg.repo}"
        self._client = httpx.Client(
            base_url=cfg.api_base,
            headers={
                "Authorization": f"Bearer {cfg.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )

    def list_tags(self, limit: int = 50) -> list[TagInfo]:
        # GitHub /tags returns name + commit sha but not date/message. We
        # sort by semver name first, then enrich the top `limit` with commit
        # details — this avoids spending API calls on tags we'll drop.
        r = self._client.get(
            f"/repos/{self._owner_repo}/tags",
            params={"per_page": 100},
        )
        r.raise_for_status()
        raw = r.json()
        # Build minimal TagInfo (no date/message yet) just so the sort key works
        minimal = [TagInfo(tag=t["name"], sha=t["commit"]["sha"][:12], date="", message="") for t in raw]
        top = sort_tags_newest_first(minimal)[:limit]
        # Now enrich each kept tag with commit details
        out: list[TagInfo] = []
        for t in top:
            cr = self._client.get(f"/repos/{self._owner_repo}/commits/{t.sha}")
            cr.raise_for_status()
            cj = cr.json()
            out.append(
                TagInfo(
                    tag=t.tag,
                    sha=t.sha,
                    date=cj.get("commit", {}).get("committer", {}).get("date", ""),
                    message=(cj.get("commit", {}).get("message") or "")
                    .strip()
                    .split("\n")[0],
                )
            )
        return out

    def get_commit_diff(self, sha: str, max_chars: int = 50_000) -> str:
        r = self._client.get(
            f"/repos/{self._owner_repo}/commits/{sha}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        r.raise_for_status()
        text = r.text
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [truncated, full diff is {len(text)} chars]"

    def get_file_commits(self, path: str, limit: int = 10) -> list[Commit]:
        r = self._client.get(
            f"/repos/{self._owner_repo}/commits",
            params={"path": path, "per_page": min(limit, 100)},
        )
        r.raise_for_status()
        return [
            Commit(
                sha=c["sha"][:12],
                date=c.get("commit", {}).get("committer", {}).get("date", ""),
                message=(c.get("commit", {}).get("message") or "")
                .strip()
                .split("\n")[0],
            )
            for c in r.json()[:limit]
        ]

    def trigger_deploy(self, env: str, ref: str) -> PipelineResult:
        """Dispatch a workflow run against the given ref (tag or branch).

        Requires the configured workflow file to define `on: workflow_dispatch`
        with an `inputs.env` (or be parameterized by the ref itself).
        """
        wf = self._cfg.deploy_workflow
        r = self._client.post(
            f"/repos/{self._owner_repo}/actions/workflows/{wf}/dispatches",
            json={"ref": ref, "inputs": {"env": env}},
        )
        if r.status_code not in (201, 204):
            r.raise_for_status()
        # workflow_dispatch returns 204 with no body; build a best-effort result.
        return PipelineResult(
            pipeline_id="",
            build_number=None,
            url=f"https://github.com/{self._owner_repo}/actions/workflows/{wf}",
            state="dispatched",
        )

    def open_pr(
        self,
        branch_name: str,
        file_path: str,
        new_content: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> PRResult:
        # 1. Resolve base SHA
        r = self._client.get(f"/repos/{self._owner_repo}/git/ref/heads/{base_branch}")
        r.raise_for_status()
        base_sha = r.json()["object"]["sha"]

        # 2. Create branch
        r = self._client.post(
            f"/repos/{self._owner_repo}/git/refs",
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        r.raise_for_status()

        # 3. Get current file SHA (if it exists) for the PUT to update
        existing_sha: str | None = None
        r = self._client.get(
            f"/repos/{self._owner_repo}/contents/{file_path}",
            params={"ref": branch_name},
        )
        if r.status_code == 200:
            existing_sha = r.json().get("sha")
        elif r.status_code != 404:
            r.raise_for_status()

        # 4. Create/update the file on the branch
        payload: dict = {
            "message": title,
            "content": base64.b64encode(new_content.encode()).decode(),
            "branch": branch_name,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        r = self._client.put(
            f"/repos/{self._owner_repo}/contents/{file_path}",
            json=payload,
        )
        r.raise_for_status()

        # 5. Open PR
        r = self._client.post(
            f"/repos/{self._owner_repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": branch_name,
                "base": base_branch,
            },
        )
        r.raise_for_status()
        pr = r.json()
        return PRResult(
            pr_url=pr.get("html_url", ""),
            pr_id=pr.get("number", 0),
            branch=branch_name,
        )
