import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import AppLayout from './components/AppLayout';
import { useAuth } from './store/AuthContext';

const LoginPage = lazy(() => import('./pages/LoginPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const TalentsPage = lazy(() => import('./pages/TalentsPage'));
const PositionsPage = lazy(() => import('./pages/PositionsPage'));
const ResumesPage = lazy(() => import('./pages/ResumesPage'));
const MatchingsPage = lazy(() => import('./pages/MatchingsPage'));
const AssessmentsPage = lazy(() => import('./pages/AssessmentsPage'));
const TrainingsPage = lazy(() => import('./pages/TrainingsPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'));
const AssistantPage = lazy(() => import('./pages/AssistantPage'));
const OrganizationsPage = lazy(() => import('./pages/OrganizationsPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const AuditPage = lazy(() => import('./pages/AuditPage'));
const FilesPage = lazy(() => import('./pages/FilesPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));
const InvitePage = lazy(() => import('./pages/InvitePage'));
const InterviewRoomPage = lazy(() => import('./pages/InterviewRoomPage'));
const Loader = <div className="loading-page"><Spin size="large" tip="正在加载智才云…"/></div>;

function Protected() { const { user, loading } = useAuth(); if (loading) return Loader; return user ? <AppLayout/> : <Navigate to="/login" replace/>; }
export default function App() {
  return <Suspense fallback={Loader}><Routes>
    <Route path="/login" element={<LoginPage/>}/>
    {/* 候选人公开页：凭邀约 token 访问，无需登录（[Agent组] 邀约/笔试/面试间） */}
    <Route path="/invite/:token" element={<InvitePage/>}/>
    <Route path="/invite/:token/room" element={<InterviewRoomPage/>}/>
    <Route element={<Protected/>}>
      <Route path="/" element={<Navigate to="/dashboard" replace/>}/>
      <Route path="/dashboard" element={<DashboardPage/>}/>
      <Route path="/talents" element={<TalentsPage/>}/><Route path="/positions" element={<PositionsPage/>}/>
      <Route path="/resumes" element={<ResumesPage/>}/><Route path="/matchings" element={<MatchingsPage/>}/>
      <Route path="/assessments" element={<AssessmentsPage/>}/><Route path="/trainings" element={<TrainingsPage/>}/>
      <Route path="/knowledge" element={<KnowledgePage/>}/><Route path="/analytics" element={<AnalyticsPage/>}/>
      <Route path="/assistant" element={<AssistantPage/>}/><Route path="/organizations" element={<OrganizationsPage/>}/>
      <Route path="/users" element={<UsersPage/>}/><Route path="/files" element={<FilesPage/>}/>
      <Route path="/audit" element={<AuditPage/>}/><Route path="/profile" element={<ProfilePage/>}/>
    </Route>
    <Route path="*" element={<Navigate to="/dashboard" replace/>}/>
  </Routes></Suspense>;
}
