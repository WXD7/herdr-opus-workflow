#!/usr/bin/env python3
"""Provision this local LangWatch through its authenticated, supported UI/REST APIs.

Contracts: deployed official 3.17.0 image and source commit 88566e6991a9326cd2b61b3ac4ac722a2f2eb2fc.
No database writes, auth bypass, remote requests, global configuration, or LLM calls.
All credentials remain in this directory's private/ files; stdout contains only IDs.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private"
SOURCE_COMMIT = "88566e6991a9326cd2b61b3ac4ac722a2f2eb2fc"


class ProvisionError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProvisionError("Unexpected redirect refused; credentials remain on loopback")


def save(path, data):
    if path.is_symlink():
        raise ProvisionError("Refusing a symlink in private state")
    content = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def stable_code(body):
    """Never echo response bodies, error messages, tokens or passwords."""
    if isinstance(body, dict):
        value = body.get("code") or body.get("error")
        if isinstance(value, dict):
            return stable_code(value.get("json", value))
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_ -]{1,100}", value):
            return value
    return "response withheld"


class Client:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar),
        )

    def request(self, method, path, body=None, token=None, project_id=None, expected=None):
        headers = {"Accept": "application/json", "Origin": self.endpoint}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if token:
            headers["Authorization"] = "Bearer " + token
        if project_id:
            headers["X-Project-Id"] = project_id
        req = urllib.request.Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=60) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        except (urllib.error.URLError, TimeoutError):
            raise ProvisionError("Local server unavailable or request timed out") from None
        try:
            result = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ProvisionError(f"{method} {path.split('?')[0]} returned non-JSON HTTP {status}") from None
        if expected is not None and status == expected:
            return result
        if status < 200 or status >= 300:
            raise ProvisionError(f"{method} {path.split('?')[0]} HTTP {status}: {stable_code(result)}")
        if expected is not None:
            raise ProvisionError(f"{method} {path.split('?')[0]} expected HTTP {expected}, got {status}")
        return result

    def trpc(self, procedure, payload, query=False):
        path = "/api/trpc/" + procedure
        wrapped = {"json": payload}
        if query:
            path += "?input=" + urllib.parse.quote(json.dumps(wrapped))
            result = self.request("GET", path)
        else:
            result = self.request("POST", path, wrapped)
        if "error" in result:
            raise ProvisionError(f"tRPC {procedure} failed: {stable_code(result)}")
        try:
            return result["result"]["data"]["json"]
        except (KeyError, TypeError):
            raise ProvisionError(f"tRPC {procedure} returned an unsupported response shape") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:5560")
    parser.add_argument("--organization", default="Herdr Local Observability")
    parser.add_argument("--project", default="herdr-workflow-fresh-20260926")
    parser.add_argument("--auth-contract", choices=("image-3.17.0", "source-main"), default="image-3.17.0",
                        help="Use the registration contract verified in the deployed official image")
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    parsed = urllib.parse.urlparse(endpoint)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ProvisionError("Only an http://127.0.0.1:<port> endpoint is permitted")
    os.umask(0o077)
    if PRIVATE.is_symlink():
        raise ProvisionError("Refusing a symlink private directory")
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    state_path = PRIVATE / "bootstrap-state.json"
    state = read_json(state_path) or {"endpoint": endpoint, "source_contract_commit": SOURCE_COMMIT}
    if state["endpoint"] != endpoint:
        raise ProvisionError("Saved state belongs to another endpoint")
    client = Client(endpoint)
    state["auth_contract"] = args.auth_contract

    login_path = PRIVATE / "dashboard-login.json"
    login = read_json(login_path)
    if not login:
        login = {"url": endpoint + "/auth/signin", "email": "herdr-local@localhost.invalid",
                 "password": "Hw9!" + secrets.token_urlsafe(24), "registered": False}
        save(login_path, login)
    if not login.get("registered"):
        enrollment = {"email": login["email"], "name": "Herdr Local", "password": login["password"]}
        if args.auth_contract == "source-main":
            proof = client.trpc("auth.requestSignUpVerification", {"email": login["email"]})
            if proof.get("sent") is not False or not proof.get("addressProof"):
                raise ProvisionError("Server requires mailbox verification; no email was configured for this local setup")
            enrollment["addressProof"] = proof["addressProof"]
        user = client.trpc("user.register", enrollment)
        login.update(registered=True, user_id=user["id"])
        save(login_path, login)
    client.request("POST", "/api/auth/sign-in/email", {"email": login["email"], "password": login["password"]})
    state["dashboard_user_id"] = login["user_id"]
    save(state_path, state)

    if "organization" not in state:
        orgs = client.trpc("organization.getAll", {}, query=True)
        candidates = orgs if isinstance(orgs, list) else orgs.get("organizations", [])
        matched = [org for org in candidates if org.get("name") == args.organization]
        if not matched:
            client.trpc("organization.createAndAssign", {"orgName": args.organization, "primaryIntent": "LLM_OPS"})
            orgs = client.trpc("organization.getAll", {}, query=True)
            candidates = orgs if isinstance(orgs, list) else orgs.get("organizations", [])
            matched = [org for org in candidates if org.get("name") == args.organization]
        if len(matched) != 1:
            raise ProvisionError("Organization readback did not identify exactly one local organization")
        organization = matched[0]
        teams = [team for team in organization.get("teams", []) if team.get("name") == args.organization]
        if len(teams) != 1:
            raise ProvisionError("Default team readback did not identify exactly one local team")
        state["organization"] = {key: organization[key] for key in ("id", "name", "slug")}
        state["team"] = {key: teams[0][key] for key in ("id", "name", "slug")}
        save(state_path, state)

    if "org_admin_key_id" not in state:
        key = client.trpc("apiKey.create", {"organizationId": state["organization"]["id"],
            "name": "Herdr local bootstrap admin", "keyType": "service", "permissionMode": "all",
            "bindings": [{"role": "ADMIN", "scopeType": "ORGANIZATION", "scopeId": state["organization"]["id"]}]})
        save(PRIVATE / "org-admin-key", key["token"] + "\n")
        state["org_admin_key_id"] = key["apiKey"]["id"]
        save(state_path, state)
    admin = (PRIVATE / "org-admin-key").read_text().strip()

    if "project" not in state:
        created = client.request("POST", "/api/projects", {"name": args.project, "language": "typescript",
            "framework": "opentelemetry", "teamId": state["team"]["id"]}, admin)
        save(PRIVATE / "project-admin-key", created["serviceApiKey"] + "\n")
        state["project"] = {key: created[key] for key in ("id", "name", "slug", "teamId")}
        state["project_admin_key_id"] = created["serviceApiKeyId"]
        save(state_path, state)
    project_id = state["project"]["id"]

    for label, permissions in (("ingest", ["traces:create"]),
                               ("read", ["traces:view", "cost:view", "analytics:view"])):
        id_field = label + "_key_id"
        if id_field not in state:
            key = client.request("POST", "/api/api-keys", {"name": "Herdr local " + label,
                "keyType": "service", "permissionMode": "restricted", "permissions": permissions,
                "bindings": [{"role": "CUSTOM", "scopeType": "PROJECT", "scopeId": project_id}]}, admin)
            save(PRIVATE / (label + "-key"), key["token"] + "\n")
            state[id_field] = key["apiKey"]["id"]
            save(state_path, state)
        detail = client.request("GET", "/api/api-keys/" + state[id_field], token=admin)
        if (detail.get("permissionMode") != "restricted" or
                sorted(detail.get("permissions", [])) != sorted(permissions) or
                detail.get("bindings") != [{"role": "CUSTOM", "scopeType": "PROJECT", "scopeId": project_id}]):
            raise ProvisionError(label + " key permission readback did not match requested scope")
        state[label + "_permissions"] = permissions
        save(state_path, state)

    # Authenticated negative tests prove the write and read capabilities stay separate.
    ingest = (PRIVATE / "ingest-key").read_text().strip()
    read = (PRIVATE / "read-key").read_text().strip()
    client.request("GET", "/api/traces/" + "0" * 32, token=ingest, project_id=project_id, expected=403)
    client.request("POST", "/api/collector", {}, read, project_id=project_id, expected=403)
    state["permission_negative_checks"] = {"ingest_cannot_read": True, "read_cannot_ingest": True}
    state["dashboard_url"] = endpoint + "/" + state["project"]["slug"] + "/traces"
    state["complete"] = True
    save(state_path, state)
    save(ROOT / "project.json", {"endpoint": endpoint, "organization": state["organization"],
        "project": state["project"], "dashboard_url": state["dashboard_url"],
        "ingest_key_file": "private/ingest-key", "read_key_file": "private/read-key",
        "permissions": {"ingest": state["ingest_permissions"], "read": state["read_permissions"]}})
    print(json.dumps({"status": "ready", "project_id": project_id,
        "dashboard_url": state["dashboard_url"], "credentials": "private/dashboard-login.json",
        "permission_negative_checks": state["permission_negative_checks"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ProvisionError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
