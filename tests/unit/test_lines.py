from collections import Counter

from commit_anatomy.lines import blank_labels, count_ranges, rle_decode, rle_encode, split_lines


def test_split_lines_matches_git_line_numbering():
    assert split_lines("") == []
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("\n\n") == ["", ""]


def test_blank_labels():
    assert blank_labels("a\n  \n\tb\n\r\n") == ["raw", "blank", "raw", "blank"]


def test_rle_roundtrip():
    labels = ["code", "code", "blank", "comment", "comment", "code"]
    runs = rle_encode(labels)
    assert runs == [["code", 2], ["blank", 1], ["comment", 2], ["code", 1]]
    assert rle_decode(runs) == labels
    assert rle_encode([]) == []


def test_count_ranges_reads_labels_and_marks_missing_lines_unlexed():
    labels = ["code", "comment", "blank"]
    assert count_ranges(labels, ((2, 2), (5, 1))) == Counter(
        {"comment": 1, "blank": 1, "unlexed": 1}
    )
    assert count_ranges(labels, ()) == Counter()
