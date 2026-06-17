import React from 'react';
import { useParams, Link } from 'react-router-dom';

function IssueDetailView() {
  const { issueId } = useParams();
  return (
    <div>
      <h2>Issue Detail View</h2>
      <p>Viewing details for Issue ID: {issueId}</p>
      <Link to="/projects">Back to Projects</Link>
      {/* Placeholder for Issue Detail content */}
    </div>
  );
}

export default IssueDetailView;