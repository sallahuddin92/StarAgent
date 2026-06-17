import React from 'react';
import { useNavigate } from 'react-router-dom';

function IssueCreateForm() {
  const navigate = useNavigate();

  const handleSubmit = (e) => {
    e.preventDefault();
    // Logic to handle issue creation
    console.log('Submitting issue form...');
    navigate('/projects');
  };

  return (
    <div>
      <h2>Create New Issue</h2>
      <form onSubmit={handleSubmit}>
        <label>Title:</label>
        <input type="text" name="title" required />
        <label>Description:</label>
        <textarea name="description" required />
        <button type="submit">Create Issue</button>
      </form>
    </div>
  );
}

export default IssueCreateForm;