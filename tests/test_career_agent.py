import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services import career_agent
from services.groq_client import AIError
from schemas.career_agent import CareerProfile,JobRequirements,GeneralAssessment

class GenericCareerTests(unittest.TestCase):
    def test_nurse_profile_has_no_fixed_technology_taxonomy(self):
        model=CareerProfile(directions=[{'name':'Clinical nursing','role_titles':['Registered Nurse'],'evidence_ids':['R1']}],skills=[{'name':'Patient care','evidence_ids':['R1']}])
        result=career_agent.build_profile({'id':'nurse','file_name':'cv.pdf','resume_text':'Registered Nurse providing patient care.'},caller=Mock(return_value=model))
        self.assertEqual(result['tracks'],['clinical nursing'])
        self.assertIn('patient care',result['skills'])
    def test_unknown_profile_evidence_is_rejected(self):
        model=CareerProfile(directions=[{'name':'Accounting','role_titles':['Accountant'],'evidence_ids':['R99']}],skills=[{'name':'Tax','evidence_ids':['R1']}])
        with self.assertRaises(AIError):career_agent.build_profile({'id':'a','resume_text':'Accountant skilled in tax.'},caller=Mock(return_value=model))
    def test_threshold_cannot_override_mandatory_gap(self):
        req=JobRequirements(requirements=[{'id':'q1','description':'Patient care','job_evidence_ids':['J1'],'mandatory':False,'weight':3},{'id':'q2','description':'Nursing license','job_evidence_ids':['J2'],'mandatory':True,'weight':1}])
        out=GeneralAssessment(criteria=[{'requirement_id':'q1','coverage':'met','resume_evidence_ids':['R1'],'explanation':'Documented care.'},{'requirement_id':'q2','coverage':'not_evidenced','resume_evidence_ids':[],'explanation':'Not documented.'}],suggestions=['Add a valid license only if held.'])
        result=career_agent.assess('Patient care\nLicense required','Patient care experience',req,threshold=70,caller=Mock(return_value=out))
        self.assertEqual(result['score'],75)
        self.assertTrue(result['threshold_met'])
        self.assertFalse(result['eligible_for_automation'])
        self.assertEqual(result['mandatory_blockers'],['Nursing license'])
    def test_omitted_and_unbacked_criteria_are_rejected(self):
        req=JobRequirements(requirements=[{'id':'q1','description':'Care','job_evidence_ids':['J1'],'mandatory':True,'weight':1}])
        out=GeneralAssessment(criteria=[{'requirement_id':'q1','coverage':'met','resume_evidence_ids':[],'explanation':'Assumed'}],suggestions=[])
        with self.assertRaises(AIError):career_agent.assess('Care','Resume',req,caller=Mock(return_value=out))

class ThresholdSelectionTests(unittest.TestCase):
    def test_any_profession_is_retrieved_and_threshold_selects_eligible_resume(self):
        from services.job_discovery import rank_job,qualify_job
        from schemas.discovery import DiscoveryPreferences
        resumes=[{'id':'a','resume_text':'Resume A'},{'id':'b','resume_text':'Resume B'}]
        profiles=[{'id':r['id'],'name':r['id']+'.pdf','hash':'hash-'+r['id'],'directions':[{'name':'Clinical nursing','role_titles':['Registered Nurse'],'evidence_ids':['R1']}],'skills':['patient care','clinical records']} for r in resumes]
        job={'title':'Registered Nurse','company':'Hospital','location':'India','description':'Provide patient care and maintain clinical records. Nursing license required.','url':'https://example.com/nurse'}
        prefs=DiscoveryPreferences().model_dump()
        retrieval=rank_job(job,profiles,prefs)
        self.assertEqual(retrieval['track'],'clinical nursing')
        def assessor(jd,text,threshold):
            return {'score':95 if text=='Resume A' else 85,'threshold_met':True,'mandatory_blockers':['License'] if text=='Resume A' else [],'eligible_for_automation':text=='Resume B'}
        result=qualify_job(retrieval,profiles,resumes,prefs,assessor=assessor)
        self.assertEqual(result['resume_id'],'b')
        self.assertEqual(result['match_score'],85)
        self.assertTrue(result['automation_ready'])
    def test_below_threshold_result_is_not_recommended(self):
        from services.job_discovery import qualify_job
        profile={'id':'a','name':'a.pdf','hash':'h'}
        job={'description':'JD','title':'Accountant'}
        result=qualify_job(job,[profile],[{'id':'a','resume_text':'Resume'}],{'matching_threshold':80},assessor=lambda *args:{'score':79,'threshold_met':False,'mandatory_blockers':[],'eligible_for_automation':False})
        self.assertIsNone(result)
