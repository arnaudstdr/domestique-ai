import { NavLink } from "react-router-dom";

const ITEMS: { to: string; label: string; icon: string }[] = [
  { to: "/", label: "Dashboard", icon: "📊" },
  { to: "/activites", label: "Activités", icon: "🚴" },
  { to: "/matin", label: "Matin", icon: "🌅" },
  { to: "/plan", label: "Plan", icon: "📋" },
  { to: "/coach", label: "Coach", icon: "🤖" },
];

export default function BottomNav() {
  return (
    <nav
      className="fixed bottom-0 inset-x-0 z-[1100] bg-card/95 backdrop-blur
                 border-t border-white/5 pb-[env(safe-area-inset-bottom)]
                 will-change-transform [transform:translateZ(0)]
                 [-webkit-backface-visibility:hidden]"
    >
      <ul className="grid grid-cols-5">
        {ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 py-2.5 text-xs ${
                  isActive ? "text-accent" : "text-muted"
                }`
              }
            >
              <span className="text-lg leading-none">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
