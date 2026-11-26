from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.proposal import reviewer_panel as panel
from autopaperreview.proposal.data import digest, write


def clean_judge(_):
    return {"verdict": "clean"}


class ReviewerPanelTest(TestCase):
    def test_only_full_source_spans_can_enter_forecast(self):
        source = {"u1": "Investigate whether the two proposed groupings are equivalent."}
        row = {"id": "toy", "overall": 3.5, "source_audit": {"source_units": source}}
        texts = ["Investigate groupings.", "Question about costs."]
        def call(stage, body, cache, offline, *, leak_judge):
            if stage == "source-review":
                return "g", dict(state="response", output={
                    "s1":{"label":"partial", "evidence":[["u1","groupings"]]},
                    "s2":{"label":"full", "evidence":[["u1","proposed groupings"]]},
                })
            self.assertEqual(body, {"s1": texts[1]})
            return "f", dict(state="response", output=[dict(n=20, grade=4, pro=["value",2,"s1"], con=None)])
        with patch.object(panel, "material", return_value=texts), patch.object(panel,"request",side_effect=call):
            r = panel.evaluate(row,Path("."),True,leak_judge=clean_judge)
        pro = r["profiles"][0]["pro"]
        self.assertEqual(pro["original_id"],"s2")
        for q in pro["evidence"]:
            self.assertEqual(q["quote"], source[q["unit"]][q["start"]:q["end"]])
        self.assertEqual(r["incumbent_score"],3.5)
        self.assertEqual(r["reason_distribution"]["con"]["probabilities"]["none"],1.)
        self.assertEqual(r["simulated_grade"],{"mean":4., "sd":0.})

    def test_rejects_invalid_forecasts_and_nonliteral_source_citations(self):
        for n,g,reason in [(19,4,None),(20,True,None),(20,4,["value",2,"s2"])]:
            with self.subTest(n=n,g=g,reason=reason),self.assertRaises(ValueError):
                panel.validate_forecast([dict(n=n,grade=g,pro=reason,con=None)],{"s1":"toy"})
        with self.assertRaises(ValueError):
            panel.validate_source({"s1":{"label":"full","evidence":[["u","missing"]]}},
                                  {"statements":{"s1":"toy"},"sources":{"u":"existing exact span"}})

    def test_empty_and_failed_source_reads_abstain_and_cached_only_never_calls(self):
        with TemporaryDirectory() as d, patch.object(panel, "run_command") as live:
            row={"id":"empty","overall":None,"proposal":{}}
            result=panel.evaluate(row,Path(d),True,leak_judge=clean_judge)
            self.assertIsNone(result["simulated_grade"])
            self.assertEqual(result["state"],"insufficient_verified_material")
            with self.assertRaises(ValueError):
                panel.request("forecast",{"s1":"A hypothesis."},Path(d),True,leak_judge=clean_judge)
            live.assert_not_called()

    def test_will_not_trust_a_malformed_cached_mass(self):
        with TemporaryDirectory() as d:
            body={"s1":"A hypothesis."}
            desc=dict(version=1,model="claude-sonnet-5-5",command=digest(panel.claude_argv()),input=digest(body),prompt=digest(panel.FORECAST))
            path=Path(d)/"forecast"/(digest(desc)+".json")
            write(path,dict(request=desc,state="response",output=[dict(n=1,grade=4,pro=None,con=None)]))
            with self.assertRaises(ValueError):
                panel.request("forecast",body,Path(d),True,leak_judge=clean_judge)
