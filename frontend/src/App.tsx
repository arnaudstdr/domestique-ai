import { Link, Route, Routes } from "react-router-dom";
import BottomNav from "./components/BottomNav";
import Dashboard from "./pages/Dashboard";
import Activities from "./pages/Activities";
import ActivityDetail from "./pages/ActivityDetail";
import Morning from "./pages/Morning";
import Coach from "./pages/Coach";
import Plan from "./pages/Plan";
import Profil from "./pages/Profil";
import Tendances from "./pages/Tendances";
import Login from "./pages/Login";

function AuthenticatedLayout() {
  return (
    <div className="min-h-screen bg-surface text-gray-100">
      <header
        className="sticky top-0 z-[1100] bg-surface/95 backdrop-blur border-b border-white/5
                   pt-[env(safe-area-inset-top)]"
      >
        <div className="mx-auto max-w-3xl px-4 py-3 flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-wide">
            <img
              src="/icon-48.png"
              alt=""
              aria-hidden="true"
              className="h-6 w-6 rounded-md"
            />
            DomestiqueAI
          </h1>
          <Link
            to="/profil"
            aria-label="Profil"
            title="Profil & paramètres"
            className="text-xl leading-none text-gray-300 hover:text-accent transition-colors"
          >
            ⚙️
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 pt-4 pb-24">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/activites" element={<Activities />} />
          <Route path="/activites/:id" element={<ActivityDetail />} />
          <Route path="/matin" element={<Morning />} />
          <Route path="/coach" element={<Coach />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/tendances" element={<Tendances />} />
          <Route path="/profil" element={<Profil />} />
        </Routes>
      </main>
      <BottomNav />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<AuthenticatedLayout />} />
    </Routes>
  );
}
