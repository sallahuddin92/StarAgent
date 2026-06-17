import React from 'react';
import { BrowserRouter as Router, Route, Switch } from 'react-router-dom';
import IssueListView from './components/IssueListView';
import ProjectCreationForm from './components/ProjectCreationForm';
import IssueDetailView from './components/IssueDetailView';

function App() {
  return (
    <Router>
      <div className="App">
        <h1>Issue Tracker Frontend</h1>
        <Switch>
          <Route path="/projects" component={IssueListView} />
          <Route path="/projects/new" component={ProjectCreationForm} />
          <Route path="/issues/:issueId" component={IssueDetailView} />
        </Switch>
      </div>
    </Router>
  );
}

export default App;