import { Routes, Route } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import Home from './pages/Home';
import PlanList from './pages/PlanList';
import PlanCreate from './pages/PlanCreate';
import NutritionSearch from './pages/NutritionSearch';
import Scaling from './pages/Scaling';
import Calibration from './pages/Calibration';
import Settings from './pages/Settings';
import Login from './pages/Login';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<AppLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/plans" element={<PlanList />} />
        <Route path="/plans/new" element={<PlanCreate />} />
        <Route path="/nutrition" element={<NutritionSearch />} />
        <Route path="/scaling" element={<Scaling />} />
        <Route path="/calibration" element={<Calibration />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}