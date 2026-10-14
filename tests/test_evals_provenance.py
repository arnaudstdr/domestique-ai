"""Tests de la provenance numérique (``evals.lib.provenance``)."""

from __future__ import annotations

from evals.lib import provenance


def test_extract_metric_values_covers_labels_units_and_decimals():
    text = "CTL de 52,3, TSB -12.5, FTP 250 W, FC 165 bpm, 85 %, 120 km, 70 kg, 1.05"
    extracted = dict(provenance.extract_metric_values(text))
    assert extracted["52,3"] == 52.3
    assert extracted["-12.5"] == -12.5
    assert extracted["250"] == 250.0
    assert extracted["165"] == 165.0
    assert extracted["85"] == 85.0
    assert extracted["120"] == 120.0
    assert extracted["70"] == 70.0
    assert extracted["1.05"] == 1.05


def test_extract_metric_values_ignores_bare_integers():
    extracted = provenance.extract_metric_values("Fais 3 séances dont 2 séances longues.")
    assert extracted == []


def test_corpus_numbers_reads_json_and_text():
    numbers = provenance.corpus_numbers({"ctl": 52.34, "tsb": -12.5}, "FTP 250 W")
    assert 52.3 in numbers  # arrondi à 1 décimale
    assert -12.5 in numbers
    assert 250.0 in numbers


def test_find_unprovenanced_flags_invented_values():
    corpus = provenance.corpus_numbers({"ctl": 52.3, "ftp": 250})
    missing = provenance.find_unprovenanced(
        "Ta CTL est de 52,3 et ton FTP de 300 W, avec un TSB de -12,5.",
        corpus,
    )
    assert missing == ["300", "-12,5"]


def test_find_unprovenanced_respects_allow_list_and_rounding():
    corpus = provenance.corpus_numbers({"ctl": 52.34})
    assert provenance.find_unprovenanced("CTL à 52,3.", corpus) == []
    assert provenance.find_unprovenanced("FTP à 300 W.", corpus, allow=[300.0]) == []
