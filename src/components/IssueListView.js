import React from 'react';
import { Link } from 'react-router-dom';

function IssueListView() {
  return (
    <div>
      <h2>Project Issues List</h2>
      <p><Link to="/projects/new">Create New Project</Link></p>
      {/* Placeholder for Issue List View content */}
      <p>List of projects/issues will be displayed here.</p>
    </div>
  );
}

export default IssueListView;