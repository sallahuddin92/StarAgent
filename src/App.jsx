import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import ProjectListView from './pages/ProjectListView';
import IssueDetailView from './pages/IssueDetailView';
import IssueCreateForm from './components/IssueCreateForm';

function App() {
  return (
    <Router>
      <h1>Issue Tracker</h1>
      <Routes>
        <Route path="/projects" element={<ProjectListView />} />
        <Route path="/issues/:id" element={<IssueDetailView />} />
        <Route path="/issues/new" element={<IssueCreateForm />} />
      </Routes>
    </Router>
  );
}

export default App;