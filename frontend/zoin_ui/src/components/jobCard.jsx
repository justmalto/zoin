import "./styles/jobCard.css"
function JobCard({
  title,
  status,
  updatedAt,
  appliedAt,
  resume,
  match,
  onClick
}) {
  return (
    <div className="job-card" onClick={onClick}>
      
      {/* LEFT */}
      <div className="job-left">
        <h3 className="job-title">{title}</h3>

        <p className="job-meta">
          <span>Status:</span> {status}
        </p>

        <p className="job-meta">
          <span>Latest update:</span> {updatedAt}
        </p>

        <p className="job-meta">
          <span>Date applied:</span> {appliedAt}
        </p>
      </div>

      {/* RIGHT */}
      <div className="job-right">
        <p className="resume-name">{resume}</p>
        <p className="match-label">Match</p>
        <p className="match-score">{match}%</p>
      </div>

    </div>
  );
}

export default JobCard;
