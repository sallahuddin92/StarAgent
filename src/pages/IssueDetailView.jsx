import React from 'react';
import { useParams, Link } from 'react-router-dom';

function IssueDetailView() {
  const { id } = useParams();

  return (
    <div>
      <h2>Issue Detail: {id}</h2>
      <p>This is the detail view for issue ID: {id}</p>
      <Link to="/projects">Back to Projects</Link>
    </div>
  );
}

export default IssueDetailView;