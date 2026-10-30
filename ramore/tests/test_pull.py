from ramore import box
from ramore.report.pull import FIRST_RUN_TAIL_BYTES, Read, fetch_new_lines, plan_reads
from ramore.report.state import Offset

from .conftest import FakeBox

LOG = "/var/log/app.log"


def test_first_run_reads_the_tail_from_a_line_start():
    big = FIRST_RUN_TAIL_BYTES + 100
    assert plan_reads(LOG, Offset(), Offset(7, big), None) == (
        [Read(LOG, 100, big, skip_first=True)],
        False,
    )
    assert plan_reads(LOG, Offset(), Offset(7, 50), None) == ([Read(LOG, 0, 50)], False)


def test_incremental_read_stops_at_the_stat():
    assert plan_reads(LOG, Offset(7, 40), Offset(7, 90), None) == (
        [Read(LOG, 40, 90)],
        False,
    )
    assert plan_reads(LOG, Offset(7, 90), Offset(7, 90), None) == ([], False)


def test_rotation_reads_the_rotated_tail_first():
    assert plan_reads(LOG, Offset(7, 40), Offset(8, 30), Offset(7, 60)) == (
        [Read(LOG + ".1", 40, 60), Read(LOG, 0, 30)],
        True,
    )
    # Rotated twice since the last run: the `.1` is another file.
    assert plan_reads(LOG, Offset(7, 40), Offset(9, 30), Offset(8, 60)) == (
        [Read(LOG, 0, 30)],
        True,
    )


def test_truncation_rereads_from_the_start():
    assert plan_reads(LOG, Offset(7, 90), Offset(7, 30), None) == (
        [Read(LOG, 0, 30)],
        True,
    )


def test_a_rotation_to_an_empty_log_moves_the_offset_to_it(fake_box: FakeBox):
    log = fake_box.path(box.REQUEST_LOG)
    log.parent.mkdir(parents=True)
    log.write_text("x\n")
    first = fetch_new_lines(box.REQUEST_LOG, Offset())
    log.rename(fake_box.path(box.REQUEST_LOG + ".1"))
    log.touch()
    after = fetch_new_lines(box.REQUEST_LOG, first.offset)
    assert after.offset == Offset(log.stat().st_ino, 0) and after.rotated
