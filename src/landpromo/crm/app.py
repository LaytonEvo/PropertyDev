"""CRM web app. Run with `landpromo crm serve`.

Settings (environment variables):
  CRM_DB         path to the SQLite file (default data/crm.sqlite3)
  CRM_PASSWORD   if set, the app asks for a password (any username). Set it whenever
                 the app is reachable from outside your own machine.
  CRM_USERS      comma-separated founder initials for "who" and approvals (default AC,LB)
  CRM_OFFLINE    "1" to skip planning.data.gov.uk when screening
  CRM_LAYERS     local layer config (default data/layers.toml)
"""

from __future__ import annotations

import os
import secrets
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import constraints as c
from .. import economics as econ
from .. import screening as scr
from . import rules
from .db import CRM

HERE = Path(__file__).parent
REPO = HERE.parents[2]
DEFAULT_SCENARIOS = {
    "residential": REPO / "scenarios" / "residential-3ac.toml",
    "industrial": REPO / "scenarios" / "industrial-5ac.toml",
}

security = HTTPBasic(auto_error=False)


def _auth(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    password = os.environ.get("CRM_PASSWORD")
    if not password or request.url.path == "/healthz":
        return
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode(), password.encode()
    ):
        raise HTTPException(401, "Password required", headers={"WWW-Authenticate": "Basic"})


def _num(v) -> float | None:
    v = (v or "").strip().replace(",", "").replace("£", "")
    return float(v) if v else None


def _go(url: str, **params) -> RedirectResponse:
    params = {k: v for k, v in params.items() if v}
    return RedirectResponse(url + ("?" + urlencode(params) if params else ""), status_code=303)


def run_screen(site, offline: bool, layers_path: str) -> tuple[str, str, float | None, str | None]:
    """Screen a CRM site; returns (markdown, rating, expected_profit, nutrient catchment names)."""
    if site["lat"] is None or site["lon"] is None or not site["gross_acres"]:
        raise ValueError("Add latitude, longitude and gross acres before screening.")
    geom = c.site_from_point(site["lat"], site["lon"], site["gross_acres"])
    layers = c.load_local_layers(layers_path)
    client = None if offline else c.PlanningDataClient()
    hits = c.lookup_constraints(geom, site["land_use"], client=client, local_layers=layers)
    s, terms, plan = econ.load_scenario(DEFAULT_SCENARIOS[site["land_use"]])
    s = replace(s, gross_acres=site["gross_acres"], land_use=site["land_use"])
    economics = econ.evaluate(s, terms, plan)
    inp = scr.ScreeningInput(
        name=site["name"], land_use=site["land_use"], gross_acres=site["gross_acres"],
        lat=site["lat"], lon=site["lon"], constraints=hits, economics=economics,
        notes=site["notes"] or "",
        sources_checked=([] if client is None else ["planning.data.gov.uk"]) + [l.name for l in layers],
    )
    result = scr.rules_rating(inp)
    narrative = scr.draft_narrative(inp, result) if os.environ.get("ANTHROPIC_API_KEY") else None
    nutrient_checked = any(l.name.startswith("nutrient") for l in layers)
    nutrient = ", ".join(h.name or h.label for h in hits if h.source.startswith("nutrient"))
    return (
        scr.render_markdown(inp, result, narrative),
        result.rating,
        economics.expected_profit,
        (nutrient or "None") if nutrient_checked else None,
    )


def create_app(db_path: str | None = None) -> FastAPI:
    crm = CRM(db_path or os.environ.get("CRM_DB", str(REPO / "data" / "crm.sqlite3")))
    users = [u.strip() for u in os.environ.get("CRM_USERS", "AC,LB").split(",") if u.strip()]
    app = FastAPI(title="Land CRM", dependencies=[Depends(_auth)], docs_url=None, redoc_url=None)
    app.state.crm = crm
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["gbp"] = lambda v: "—" if v is None else f"£{v:,.0f}"
    templates.env.globals.update(
        STAGES=rules.STAGES, ACTIVITY_KINDS=rules.ACTIVITY_KINDS,
        SPEND_CATEGORIES=rules.SPEND_CATEGORIES, USERS=users, EXIT_CRITERIA=rules.EXIT_CRITERIA,
        OUTBOUND_KINDS=rules.OUTBOUND_KINDS, today=lambda: date.today().isoformat(),
    )

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("error", request.query_params.get("error"))
        ctx.setdefault("msg", request.query_params.get("msg"))
        return templates.TemplateResponse(request, name, ctx)

    def get_site(site_id: int):
        s = crm.site(site_id)
        if s is None:
            raise HTTPException(404, "Site not found")
        return s

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # --- dashboard ----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        counts = crm.stage_counts()
        live = [s for s in crm.sites() if s["stage"] not in rules.CLOSED_STAGES]
        return page(
            request, "dashboard.html",
            counts=counts, live_count=len(live),
            total_spent=crm.total_spent(),
            committed=sum(s["spent"] for s in live),
            follow_ups=crm.follow_ups_due(),
            stalled=crm.stalled_sites(),
            deadlines=crm.upcoming_deadlines(),
            recent=crm.activities(limit=10),
            stall_days=rules.STALL_DAYS,
        )

    # --- sites --------------------------------------------------------------
    @app.get("/sites", response_class=HTMLResponse)
    def sites(request: Request, stage: str | None = None):
        rows = crm.sites(stage)
        caps = {s["id"]: rules.spend_cap(crm.site_state(s["id"])) for s in rows}
        return page(request, "sites.html", sites=rows, stage=stage, caps=caps)

    @app.get("/sites/new", response_class=HTMLResponse)
    def site_new(request: Request):
        return page(request, "site_form.html", site=None)

    def _site_data(form) -> dict:
        data = {f: (form.get(f) or "").strip() for f in
                ("name", "authority", "land_use", "nutrient_catchment", "local_plan_stage",
                 "next_deadline", "next_deadline_note", "notes")}
        for f in ("gross_acres", "lat", "lon", "legal_budget", "agreement_budget"):
            data[f] = _num(form.get(f))
        data["legal_budget"] = data["legal_budget"] or 0
        data["agreement_budget"] = data["agreement_budget"] if data["agreement_budget"] is not None else 75_000
        data["next_deadline"] = data["next_deadline"] or None
        if data["land_use"] not in ("residential", "industrial"):
            data["land_use"] = "residential"
        return data

    @app.post("/sites")
    async def site_create(request: Request):
        form = await request.form()
        data = _site_data(form)
        if not data["name"]:
            return _go("/sites/new", error="Name is required.")
        site_id = crm.create_site(data, by_whom=form.get("by_whom", ""))
        return _go(f"/sites/{site_id}", msg="Site created.")

    @app.get("/sites/{site_id}", response_class=HTMLResponse)
    def site_detail(request: Request, site_id: int):
        s = get_site(site_id)
        state = crm.site_state(site_id)
        spent = crm.spent(site_id)
        cap = rules.spend_cap(state)
        linked = crm.site_landowners(site_id)
        linked_ids = {l["id"] for l in linked}
        return page(
            request, "site.html", site=s, spent=spent, cap=cap,
            landowners=linked,
            other_landowners=[l for l in crm.landowners() if l["id"] not in linked_ids],
            activities=crm.activities(site_id=site_id),
            spend=crm.spend(site_id), history=crm.stage_history(site_id),
            next_stage=rules.STAGES[min(rules.STAGES.index(s["stage"]) + 1, len(rules.STAGES) - 2)],
        )

    @app.get("/sites/{site_id}/edit", response_class=HTMLResponse)
    def site_edit_form(request: Request, site_id: int):
        return page(request, "site_form.html", site=get_site(site_id))

    @app.post("/sites/{site_id}/edit")
    async def site_edit(request: Request, site_id: int):
        get_site(site_id)
        crm.update_site(site_id, _site_data(await request.form()))
        return _go(f"/sites/{site_id}", msg="Saved.")

    @app.post("/sites/{site_id}/stage")
    async def site_stage(request: Request, site_id: int):
        get_site(site_id)
        form = await request.form()
        new_stage = form.get("new_stage", "")
        kill_reason = form.get("kill_reason", "")
        override_by = (form.get("override_by") or "").strip()
        problems = rules.stage_change_problems(crm.site_state(site_id), new_stage, kill_reason)
        if new_stage == "Killed" and problems:
            return _go(f"/sites/{site_id}", error=" ".join(problems))
        if problems and not override_by:
            return _go(f"/sites/{site_id}",
                       error="Can't move stage yet: " + " ".join(problems)
                       + " A founder can override by entering initials.")
        note = form.get("note", "")
        if problems:
            note = f"OVERRIDE by {override_by}: {'; '.join(problems)}. {note}".strip()
        crm.change_stage(site_id, new_stage, form.get("by_whom", ""), note, kill_reason)
        return _go(f"/sites/{site_id}", msg=f"Moved to {new_stage}.")

    @app.post("/sites/{site_id}/spend")
    async def site_spend(request: Request, site_id: int):
        get_site(site_id)
        form = await request.form()
        amount = _num(form.get("amount"))
        if not amount or amount <= 0:
            return _go(f"/sites/{site_id}", error="Enter a spend amount.")
        approved_by = (form.get("approved_by") or "").strip()
        err = rules.check_spend(crm.site_state(site_id), crm.spent(site_id), amount, approved_by)
        if err:
            return _go(f"/sites/{site_id}", error=err)
        crm.add_spend(site_id, {
            "date": form.get("date") or date.today().isoformat(), "amount": amount,
            "category": form.get("category", "Other"), "supplier": form.get("supplier", ""),
            "description": form.get("description", ""), "approved_by": approved_by,
        })
        return _go(f"/sites/{site_id}", msg="Spend recorded.")

    @app.post("/sites/{site_id}/activities")
    async def site_activity(request: Request, site_id: int):
        get_site(site_id)
        form = await request.form()
        return await _add_activity(form, site_id=site_id, back=f"/sites/{site_id}")

    async def _add_activity(form, site_id: int | None, back: str):
        lid = int(form["landowner_id"]) if form.get("landowner_id") else None
        kind = form.get("kind", "Note")
        if lid and kind in rules.OUTBOUND_KINDS:
            owner = crm.landowner(lid)
            if owner["do_not_contact"]:
                return _go(back, error=f"{owner['name']} is marked do-not-contact.")
        when = form.get("date") or date.today().isoformat()
        follow = form.get("follow_up_date") or None
        if not follow and form.get("auto_follow_up"):
            d = rules.default_follow_up(kind, date.fromisoformat(when))
            follow = d.isoformat() if d else None
        if lid and kind in ("Landowner replied", "Meeting"):
            crm.close_follow_ups_for(lid)
        crm.add_activity({
            "site_id": site_id, "landowner_id": lid, "date": when, "kind": kind,
            "summary": form.get("summary", ""), "by_whom": form.get("by_whom", ""),
            "follow_up_date": follow,
        })
        warning = None
        if lid and kind == "Letter sent" and not crm.landowner(lid)["privacy_notice_sent"]:
            warning = "Logged. No privacy notice recorded for this landowner yet (UK GDPR Article 14)."
        return _go(back, msg=None if warning else "Activity logged.", error=warning)

    @app.post("/sites/{site_id}/landowners")
    async def site_link(request: Request, site_id: int):
        get_site(site_id)
        form = await request.form()
        if form.get("landowner_id"):
            lid = int(form["landowner_id"])
        elif (form.get("name") or "").strip():
            lid = crm.create_landowner({"name": form["name"].strip(),
                                        "owner_type": form.get("owner_type", "Individual")})
        else:
            return _go(f"/sites/{site_id}", error="Pick a landowner or enter a name.")
        crm.link(site_id, lid, form.get("role") or "Owner")
        return _go(f"/sites/{site_id}", msg="Landowner linked.")

    @app.post("/sites/{site_id}/landowners/{lid}/unlink")
    def site_unlink(site_id: int, lid: int):
        crm.unlink(site_id, lid)
        return _go(f"/sites/{site_id}", msg="Landowner unlinked.")

    @app.post("/sites/{site_id}/screen")
    def site_screen(site_id: int):
        s = get_site(site_id)
        try:
            md, rating, ev, nutrient = run_screen(
                s, offline=os.environ.get("CRM_OFFLINE") == "1",
                layers_path=os.environ.get("CRM_LAYERS", str(REPO / "data" / "layers.toml")),
            )
        except Exception as e:  # show any data/API failure to the user rather than a 500
            return _go(f"/sites/{site_id}", error=f"Screen failed: {e}")
        crm.save_appraisal(site_id, md, rating, ev, nutrient)
        return _go(f"/sites/{site_id}", msg=f"Screen complete: {rating}.")

    # --- landowners ---------------------------------------------------------
    @app.get("/landowners", response_class=HTMLResponse)
    def landowners(request: Request):
        return page(request, "landowners.html", landowners=crm.landowners())

    @app.get("/landowners/new", response_class=HTMLResponse)
    def landowner_new(request: Request):
        return page(request, "landowner_form.html", owner=None)

    def _owner_data(form) -> dict:
        data = {f: (form.get(f) or "").strip() for f in
                ("name", "owner_type", "address", "email", "phone", "lawful_basis",
                 "privacy_notice_sent", "notes")}
        data["privacy_notice_sent"] = data["privacy_notice_sent"] or None
        data["do_not_contact"] = 1 if form.get("do_not_contact") else 0
        return data

    @app.post("/landowners")
    async def landowner_create(request: Request):
        data = _owner_data(await request.form())
        if not data["name"]:
            return _go("/landowners/new", error="Name is required.")
        return _go(f"/landowners/{crm.create_landowner(data)}", msg="Landowner created.")

    @app.get("/landowners/{lid}", response_class=HTMLResponse)
    def landowner_detail(request: Request, lid: int):
        owner = crm.landowner(lid)
        if owner is None:
            raise HTTPException(404, "Landowner not found")
        return page(request, "landowner.html", owner=owner, sites=crm.landowner_sites(lid),
                    activities=crm.activities(landowner_id=lid))

    @app.get("/landowners/{lid}/edit", response_class=HTMLResponse)
    def landowner_edit_form(request: Request, lid: int):
        return page(request, "landowner_form.html", owner=crm.landowner(lid))

    @app.post("/landowners/{lid}/edit")
    async def landowner_edit(request: Request, lid: int):
        crm.update_landowner(lid, _owner_data(await request.form()))
        return _go(f"/landowners/{lid}", msg="Saved.")

    @app.post("/landowners/{lid}/activities")
    async def landowner_activity(request: Request, lid: int):
        form = dict(await request.form())
        form["landowner_id"] = str(lid)
        site_id = int(form["site_id"]) if form.get("site_id") else None
        return await _add_activity(form, site_id=site_id, back=f"/landowners/{lid}")

    # --- follow-ups ---------------------------------------------------------
    @app.post("/activities/{aid}/done")
    async def activity_done(request: Request, aid: int):
        crm.complete_follow_up(aid)
        back = (await request.form()).get("next") or "/"
        return _go(back if back.startswith("/") else "/", msg="Follow-up marked done.")

    return app
