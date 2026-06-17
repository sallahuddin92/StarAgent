import React from 'react';
import { Link } from 'react-router-dom';

function ProjectListView() {
  return (
    <div>
      <h2>Projects</h2>
      <div>
        <Link to="/issues/new">Create New Issue</Link>
      </div>
      {/* Placeholder for Project Listing View content */}
    </div>
  );
}

export default ProjectListView;