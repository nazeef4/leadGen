"""Scrape pipeline: targeting -> queries -> scrape -> dedupe -> enrich -> score.

Runs as a background job so the UI can show progress.  Progress and results live
in memory; only accepted leads are written to the database (by the caller).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ...config import Settings, get_settings
from ..geo import Place, get_geo_service
from ..niche_advisor import get_niche_advisor
from .base import BaseScraper, ScrapedLead
from .csv_import import CsvImportScraper
from .demo import DemoScraper
from .duckduckgo import DuckDuckGoScraper
from .enrich import Enricher, score_lead
from .google_places import GooglePlacesScraper

log = logging.getLogger("leadgen.pipeline")

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "duckduckgo": DuckDuckGoScraper,
    "google_places": GooglePlacesScraper,
    "demo": DemoScraper,
    "csv": CsvImportScraper,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class QueryPlanItem:
    query: str
    place_label: str
    place: Place | None = None


@dataclass
class ScrapeStats:
    queries_planned: int = 0
    queries_run: int = 0
    raw_results: int = 0
    duplicates: int = 0
    enriched: int = 0
    with_email: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_dict(self) -> dict:
        duration = None
        if self.finished_at:
            duration = round((self.finished_at - self.started_at).total_seconds(), 1)
        return {
            "queriesPlanned": self.queries_planned,
            "queriesRun": self.queries_run,
            "rawResults": self.raw_results,
            "duplicates": self.duplicates,
            "enriched": self.enriched,
            "withEmail": self.with_email,
            "errors": self.errors[:20],
            "startedAt": self.started_at.isoformat(),
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "durationSeconds": duration,
        }


class QueryBuilder:
    """Turns (niche, geo selection, archetype) into a bounded list of queries."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.geo = get_geo_service()
        self.advisor = get_niche_advisor()

    def build(
        self,
        offering: str,
        geo_filter: dict | None,
        max_places: int = 12,
        queries_per_place: int = 2,
        extra_terms: list[str] | None = None,
    ) -> list[QueryPlanItem]:
        suggestion = self.advisor.suggest(offering, geo_filter, top_n=max_places, use_llm=False)
        terms = list(extra_terms or []) or suggestion["searchTerms"][:3]
        plan: list[QueryPlanItem] = []
        for item in suggestion["suggestions"]:
            place = self.geo.place(
                item.get("countryCode") or "",
                item.get("state") or "*",
                item.get("city") or "*",
            )
            location = item["label"].split(",")[0]
            qualifier = item.get("state") or item.get("country") or ""
            for term in terms[:queries_per_place]:
                query = f"{term} {location}".strip()
                if qualifier and len(query) < 34:
                    query = f"{query} {qualifier}"
                plan.append(QueryPlanItem(query=query, place_label=item["label"], place=place))
        # free-text cities that are not in the bundled dataset
        for raw in (geo_filter or {}).get("extraCities") or []:
            name = str(raw).strip()
            if not name:
                continue
            for term in terms[:queries_per_place]:
                plan.append(
                    QueryPlanItem(query=f"{term} {name}".strip(), place_label=name, place=None)
                )
        cap = self.settings.scrape_max_pages_per_campaign
        return plan[: max(1, min(len(plan), cap))]

    def search_terms(self, offering: str) -> list[str]:
        return self.advisor.suggest(offering, None, use_llm=False)["searchTerms"]


@dataclass
class JobState:
    job_id: str
    campaign_id: int | None
    status: str = "pending"  # pending | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    stats: ScrapeStats = field(default_factory=ScrapeStats)
    results: list[ScrapedLead] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    cancelled: bool = False

    def to_dict(self, include_results: bool = False) -> dict:
        data = {
            "jobId": self.job_id,
            "campaignId": self.campaign_id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "stats": self.stats.to_dict(),
            "resultCount": len(self.results),
            "createdAt": self.created_at.isoformat(),
        }
        if include_results:
            data["results"] = [r.to_dict() for r in self.results]
        return data


class ScrapePipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.query_builder = QueryBuilder(self.settings)
        self.enricher = Enricher(settings=self.settings)
        self.jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- scrapers
    def build_scraper(self, name: str, place: Place | None = None) -> BaseScraper | None:
        cls = SCRAPER_REGISTRY.get(name)
        if cls is None:
            return None
        if cls is DemoScraper and place is not None:
            return DemoScraper(
                self.settings,
                city=place.city or place.state,
                state=place.state,
                country=place.country,
            )
        scraper = cls(self.settings)
        if getattr(scraper, "requires_key", False) and not scraper.available:
            return None
        return scraper

    # ------------------------------------------------------------------ run
    def run_sync(
        self,
        offering: str,
        geo_filter: dict | None,
        *,
        campaign_id: int | None = None,
        sources: list[str] | None = None,
        max_results: int = 200,
        enrich: bool = True,
        max_places: int = 12,
        queries_per_place: int = 2,
        buyer_signals: list[str] | None = None,
        csv_text: str = "",
        job: JobState | None = None,
        on_progress: Callable[[JobState], None] | None = None,
    ) -> JobState:
        job = job or JobState(job_id=uuid.uuid4().hex[:12], campaign_id=campaign_id)
        job.campaign_id = campaign_id
        with self._lock:
            self.jobs[job.job_id] = job
        job.status = "running"
        stats = job.stats

        sources = sources or ["duckduckgo"]
        if csv_text and "csv" in sources:
            importer = CsvImportScraper(self.settings)
            rows = importer.from_text(csv_text, limit=max_results)
            stats.raw_results += len(rows)
            stats.errors.extend(f"csv row {s['row']}: {s['reason']}" for s in importer.skipped[:10])
            job.results.extend(rows)

        plan = self.query_builder.build(
            offering, geo_filter, max_places=max_places, queries_per_place=queries_per_place
        )
        stats.queries_planned = len(plan)
        seen: set[str] = {r.dedupe_key() for r in job.results}
        target_cities = {
            item.place.city for item in plan if item.place and item.place.city
        }

        total_steps = max(1, len(plan) * max(1, len(sources)))
        step = 0
        for item in plan:
            if job.cancelled:
                job.message = "Cancelled by user"
                break
            for source in sources:
                step += 1
                if job.cancelled or len(job.results) >= max_results:
                    break
                scraper = self.build_scraper(source, item.place)
                if scraper is None:
                    if f"{source} unavailable" not in stats.errors:
                        stats.errors.append(
                            f"{source} unavailable (missing API key or not installed)"
                        )
                    continue
                try:
                    found = scraper.search(item.query, limit=self.settings.scrape_results_per_query)
                except Exception as exc:  # pragma: no cover - network dependent
                    log.warning("scraper %s failed on %r: %s", source, item.query, exc)
                    stats.errors.append(f"{source}: {exc}")
                    found = []
                stats.queries_run += 1
                stats.raw_results += len(found)
                for lead in found:
                    if len(job.results) >= max_results:
                        break
                    if not lead.city and item.place:
                        lead.city = item.place.city
                        lead.state = item.place.state or lead.state
                        lead.country = item.place.country or lead.country
                    if not lead.category:
                        lead.category = offering.split(" for ")[0][:80]
                    key = lead.dedupe_key()
                    if key in seen:
                        stats.duplicates += 1
                        continue
                    seen.add(key)
                    job.results.append(lead)
                job.progress = min(0.9, step / total_steps * 0.9)
                job.message = f"Scraped {len(job.results)} leads ({stats.queries_run}/{len(plan)} queries)"
                if on_progress:
                    on_progress(job)

        if enrich and job.results and "demo" not in sources and "csv" not in sources:
            budget = min(len(job.results), 25)
            for index, lead in enumerate(job.results[:budget]):
                if job.cancelled:
                    break
                self.enricher.enrich(lead, fetch_pages=bool(lead.website and not lead.email))
                stats.enriched += 1
                job.progress = 0.9 + (index + 1) / budget * 0.08
                job.message = f"Enriching {index + 1}/{budget} websites"
                if on_progress:
                    on_progress(job)

        for lead in job.results:
            mx_ok = lead.signals.get("mxValid")
            score, reasons = score_lead(
                lead, mx_ok=mx_ok, buyer_signals=buyer_signals, target_cities=target_cities
            )
            lead.signals["scoreReasons"] = reasons
            lead.signals["score"] = score
            if lead.email:
                stats.with_email += 1

        job.results.sort(key=lambda r: -(r.signals.get("score") or 0))
        job.progress = 1.0
        job.status = "error" if (not job.results and stats.errors) else "done"
        job.message = (
            f"Found {len(job.results)} unique leads "
            f"({stats.with_email} with email, {stats.duplicates} duplicates dropped)"
        )
        stats.finished_at = utcnow()
        return job

    def start_async(self, **kwargs) -> JobState:
        job = JobState(job_id=uuid.uuid4().hex[:12], campaign_id=kwargs.get("campaign_id"))
        with self._lock:
            self.jobs[job.job_id] = job
        kwargs["job"] = job
        thread = threading.Thread(target=self._worker, kwargs=kwargs, daemon=True)
        thread.start()
        return job

    def _worker(self, **kwargs) -> None:
        try:
            self.run_sync(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            job = kwargs.get("job")
            if job is not None:
                job.status = "error"
                job.message = f"Scrape failed: {exc}"
                job.stats.finished_at = utcnow()
            log.exception("scrape job failed")

    def get_job(self, job_id: str) -> JobState | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancelled = True
        return True

    def prune_jobs(self, keep: int = 20) -> None:
        with self._lock:
            ordered = sorted(self.jobs.values(), key=lambda j: j.created_at)
            for job in ordered[:-keep]:
                self.jobs.pop(job.job_id, None)


_pipeline: ScrapePipeline | None = None


def get_pipeline() -> ScrapePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ScrapePipeline()
    return _pipeline
