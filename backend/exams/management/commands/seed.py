from django.core.management.base import BaseCommand
from django.db import transaction

from exams.models import Board, Exam, ExamStage

BOARDS = [
    {
        "code": "UPSC",
        "name": "Union Public Service Commission",
        "official_url": "https://upsc.gov.in",
        "timezone": "Asia/Kolkata",
    },
    {
        "code": "SSC",
        "name": "Staff Selection Commission",
        "official_url": "https://ssc.nic.in",
        "timezone": "Asia/Kolkata",
    },
    {
        "code": "IBPS",
        "name": "Institute of Banking Personnel Selection",
        "official_url": "https://ibps.in",
        "timezone": "Asia/Kolkata",
    },
]

EXAMS = [
    {
        "board_code": "UPSC",
        "code": "CSE",
        "name": "Civil Services Examination",
        "cycle_year": 2026,
        "category": "Civil Services",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
            (ExamStage.StageType.INTERVIEW, 3),
        ],
    },
    {
        "board_code": "UPSC",
        "code": "CDS",
        "name": "Combined Defence Services Examination",
        "cycle_year": 2026,
        "category": "Defence",
        "stages": [
            (ExamStage.StageType.MAINS, 1),
            (ExamStage.StageType.INTERVIEW, 2),
        ],
    },
    {
        "board_code": "UPSC",
        "code": "NDA",
        "name": "National Defence Academy Examination",
        "cycle_year": 2026,
        "category": "Defence",
        "stages": [
            (ExamStage.StageType.MAINS, 1),
            (ExamStage.StageType.INTERVIEW, 2),
        ],
    },
    {
        "board_code": "UPSC",
        "code": "ESE",
        "name": "Engineering Services Examination",
        "cycle_year": 2026,
        "category": "Engineering",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
            (ExamStage.StageType.INTERVIEW, 3),
        ],
    },
    {
        "board_code": "SSC",
        "code": "CGL",
        "name": "Combined Graduate Level Examination",
        "cycle_year": 2026,
        "category": "Graduate Level",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
        ],
    },
    {
        "board_code": "SSC",
        "code": "CHSL",
        "name": "Combined Higher Secondary Level Examination",
        "cycle_year": 2026,
        "category": "Higher Secondary Level",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
        ],
    },
    {
        "board_code": "SSC",
        "code": "MTS",
        "name": "Multi Tasking Staff Examination",
        "cycle_year": 2026,
        "category": "Non-Technical",
        "stages": [
            (ExamStage.StageType.SINGLE, 1),
        ],
    },
    {
        "board_code": "SSC",
        "code": "GD",
        "name": "General Duty Constable Examination",
        "cycle_year": 2026,
        "category": "Police",
        "stages": [
            (ExamStage.StageType.MAINS, 1),
            (ExamStage.StageType.SKILL, 2),
        ],
    },
    {
        "board_code": "IBPS",
        "code": "PO",
        "name": "Probationary Officer Examination",
        "cycle_year": 2026,
        "category": "Banking",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
            (ExamStage.StageType.INTERVIEW, 3),
        ],
    },
    {
        "board_code": "IBPS",
        "code": "CLERK",
        "name": "Clerk Examination",
        "cycle_year": 2026,
        "category": "Banking",
        "stages": [
            (ExamStage.StageType.PRELIMS, 1),
            (ExamStage.StageType.MAINS, 2),
        ],
    },
]


class Command(BaseCommand):
    help = "Populate a demo dataset: 10 real exams across 3 boards. Safe to re-run."

    @transaction.atomic
    def handle(self, *args, **options):
        boards_by_code = {}
        for data in BOARDS:
            board, created = Board.objects.get_or_create(
                code=data["code"],
                defaults={
                    "name": data["name"],
                    "official_url": data["official_url"],
                    "timezone": data["timezone"],
                },
            )
            boards_by_code[board.code] = board
            self._report("board", board.code, created)

        for data in EXAMS:
            board = boards_by_code[data["board_code"]]
            exam, created = Exam.objects.get_or_create(
                board=board,
                code=data["code"],
                cycle_year=data["cycle_year"],
                defaults={
                    "name": data["name"],
                    "category": data["category"],
                },
            )
            self._report("exam", str(exam), created)

            for stage_type, sequence in data["stages"]:
                _, stage_created = ExamStage.objects.get_or_create(
                    exam=exam,
                    sequence=sequence,
                    defaults={"stage_type": stage_type},
                )
                self._report("  stage", f"{exam} #{sequence} ({stage_type})", stage_created)

    def _report(self, label: str, name: str, created: bool) -> None:
        verb = "created" if created else "already exists"
        self.stdout.write(f"{label}: {name} - {verb}")
