import React from 'react';
import { Link } from 'react-router-dom';

function ProjectCreationForm() {
  return (
    <div>
      <h2>Create New Project</h2>
      <p>Form for creating a new project.</p>
      <Link to="/projects">Back to Project List</Link>
    </div>
  );
}

export default ProjectCreationForm;