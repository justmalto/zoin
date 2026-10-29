import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.resume_tailoring import propose,render_docx,FactualVerification
from services.groq_client import AIError
from schemas.career_agent import TailoringProposal
from docx import Document

class TailoringTests(unittest.TestCase):
    def test_draft_retains_untouched_lines_and_requires_review(self):
        model=TailoringProposal(edits=[{'source_id':'R3','replacement':'Developed APIs serving 20 users.','explanation':'Clarifies existing work.'}],unaddressed_gaps=['License not evidenced.'])
        draft=propose('Test Person\nEXPERIENCE\nBuilt APIs for 20 users.','API role',[],caller=Mock(side_effect=[model,FactualVerification(faithful=True)]))
        self.assertFalse(draft['approved'])
        self.assertIn('Test Person\nEXPERIENCE',draft['text'])
        doc=Document(io.BytesIO(render_docx(draft['text'])))
        self.assertEqual('\n'.join(p.text for p in doc.paragraphs),draft['text'])
    def test_new_metrics_or_unknown_sources_are_rejected(self):
        for source,replacement in [('R1','Served 500 users.'),('R99','Served 20 users.')]:
            model=TailoringProposal(edits=[{'source_id':source,'replacement':replacement,'explanation':'Change'}],unaddressed_gaps=[])
            with self.assertRaises(AIError):propose('Served 20 users.','Role',[],caller=Mock(return_value=model))

    def test_unfaithful_wording_is_rejected_before_rendering(self):
        model=TailoringProposal(edits=[{'source_id':'R1','replacement':'Led Python development.','explanation':'Stronger wording'}],unaddressed_gaps=[])
        with self.assertRaises(AIError) as result:
            propose('Used Python.','Python role',[],caller=Mock(side_effect=[model,FactualVerification(faithful=False)]))
        self.assertEqual(result.exception.status_code,422)
