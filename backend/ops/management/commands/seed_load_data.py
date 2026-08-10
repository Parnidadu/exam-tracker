"""Generate a dataset large enough for a load test to mean something.

Measuring against the ten exams `seed` creates would flatter every query:
a list endpoint that paginates twenty rows out of ten is not the endpoint
that runs in production, and neither is a filter that never has anything
to exclude.
"""

from __future__ import annotations

import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from exams.models import Board, Exam, ExamStage, StatusTrack

BOARDS = [
    ("UPSC", "Union Public Service Commission"),
    ("SSC", "Staff Selection Commission"),
    ("IBPS", "Institute of Banking Personnel Selection"),
    ("RRB", "Railway Recruitment Board"),
    ("NTA", "National Testing Agency"),
    ("MPSC", "Maharashtra Public Service Commission"),
    ("BPSC", "Bihar Public Service Commission"),
    ("TNPSC", "Tamil Nadu Public Service Commission"),
]

SUBJECTS = [
    "Civil Services", "Combined Graduate Level", "Probationary Officer",
    "Clerk", "Junior Engineer", "Assistant Commandant", "Group D",
    "Combined Defence Services", "Engineering Services", "Forest Service",
    "Statistical Service", "Economic Service", "Multi Tasking Staff",
    "Stenographer", "Constable", "Sub Inspector",
]

CATEGORIES = ["Civil Services", "Banking", "Railways", "Defence", "Teaching", "Police"]
STAGE_SETS = [
    [ExamStage.StageType.PRELIMS, ExamStage.StageType.MAINS, ExamStage.StageType.INTERVIEW],
    [ExamStage.StageType.PRELIMS, ExamStage.StageType.MAINS],
    [ExamStage.StageType.SINGLE],
    [ExamStage.StageType.MAINS, ExamStage.StageType.SKILL],
]
CONDUCT = ["scheduled", "conducted", "postponed", ""]
RESULT = ["awaited", "declared", "withheld", ""]


class Command(BaseCommand):
    help = "Create a realistically sized dataset for load testing."

    def add_arguments(self, parser):
        parser.add_argument("--exams", type=int, default=500)
        parser.add_argument("--years", type=int, default=4)
        parser.add_argument("--seed", type=int, default=20260810, help="For a repeatable dataset.")

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(options["seed"])
        now = timezone.now()
        this_year = now.year

        boards = {}
        for code, name in BOARDS:
            board, _ = Board.objects.get_or_create(
                code=code,
                defaults={"name": name, "official_url": f"https://{code.lower()}.gov.in"},
            )
            boards[code] = board

        wanted = options["exams"]
        existing = set(Exam.objects.values_list("board__code", "code", "cycle_year"))
        exams: list[Exam] = []
        seen: set[tuple[str, str, int]] = set()

        while len(exams) < wanted:
            code, _ = random.choice(BOARDS)
            subject = random.choice(SUBJECTS)
            year = this_year - random.randrange(options["years"])
            short = "".join(word[0] for word in subject.split())[:6]
            # A suffix keeps the (board, code, year) constraint satisfiable
            # once the natural combinations run out.
            exam_code = f"{short}-{len(exams):04d}"
            key = (code, exam_code, year)
            if key in seen or key in existing:
                continue
            seen.add(key)
            exams.append(
                Exam(
                    board=boards[code],
                    code=exam_code,
                    name=f"{subject} Examination",
                    cycle_year=year,
                    category=random.choice(CATEGORIES),
                    # Set explicitly: bulk_create bypasses save(), which is
                    # where slugs are normally generated.
                    slug=slugify(f"{code}-{exam_code}-{year}"),
                )
            )

        Exam.objects.bulk_create(exams, batch_size=500)
        created = list(Exam.objects.filter(slug__in=[exam.slug for exam in exams]))

        stages: list[ExamStage] = []
        for exam in created:
            for sequence, stage_type in enumerate(random.choice(STAGE_SETS), start=1):
                offset = random.randrange(-400, 400)
                stages.append(
                    ExamStage(
                        exam=exam,
                        stage_type=stage_type,
                        sequence=sequence,
                        planned_start_date=(now + timezone.timedelta(days=offset)).date(),
                        exam_date=(now + timezone.timedelta(days=offset)).date(),
                        result_date=(now + timezone.timedelta(days=offset + 45)).date(),
                    )
                )
        ExamStage.objects.bulk_create(stages, batch_size=1000)

        tracks: list[StatusTrack] = []
        for stage in ExamStage.objects.filter(exam__in=created).only("id"):
            for track, values in (
                (StatusTrack.Track.CONDUCT, CONDUCT),
                (StatusTrack.Track.RESULT, RESULT),
            ):
                value = random.choice(values)
                tracks.append(
                    StatusTrack(
                        exam_stage=stage,
                        track=track,
                        machine_value=value,
                        machine_confidence=0.9 if value else None,
                        machine_seen_at=now if value else None,
                    )
                )
        # bulk_create bypasses post_save, so this does not emit a
        # StatusChange per row (EXT-052) - which is what we want here: the
        # dataset is scenery, not a hundred thousand real observations.
        StatusTrack.objects.bulk_create(tracks, batch_size=1000, ignore_conflicts=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"{Board.objects.count()} boards, {Exam.objects.count()} exams, "
                f"{ExamStage.objects.count()} stages, {StatusTrack.objects.count()} status tracks"
            )
        )
