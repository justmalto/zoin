export default function AnalysisResult({ analysis }) {
  if (!analysis) return <p>Match a saved resume to see evidence and improvement suggestions.</p>
  return <div className="analysis-result">
    <p><strong>{analysis.match_score}% requirement coverage</strong> · {analysis.model_name}</p><p>{analysis.summary}</p>
    <p className="small muted">{analysis.score_note}</p>
    {!!analysis.strengths?.length && <><h4>Strengths</h4><ul>{analysis.strengths.map((strength, i) => <li key={i}>{typeof strength === 'string' ? strength : strength.claim}{strength.evidence?.length > 0 && <blockquote>{strength.evidence.join(' · ')}</blockquote>}</li>)}</ul></>}
    {!!analysis.gaps?.length && <><h4>Gaps to address</h4><ul>{analysis.gaps.map((gap, i) => <li key={i}>{typeof gap === 'string' ? gap : gap.explanation || gap.requirement}</li>)}</ul></>}
    {!!analysis.suggestions?.length && <><h4>Suggestions</h4><ul>{analysis.suggestions.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
    {analysis.experience_check?.documented_months_upper_bound != null && <p className="small">Dated experience: at most {analysis.experience_check.documented_months_upper_bound} calendar months, including internships and partial months.</p>}
    {analysis.criteria && <details><summary>Requirement-by-requirement evidence</summary>{analysis.criteria.map(criterion => <div key={criterion.category} className="criterion"><h4>{criterion.category.replaceAll('_', ' ')} · {criterion.coverage.replaceAll('_', ' ')}</h4><p>{criterion.requirement_quote}</p><blockquote>{criterion.resume_evidence.join(' · ') || 'No evidence established.'}</blockquote><p>{criterion.explanation}</p></div>)}</details>}
  </div>
}
