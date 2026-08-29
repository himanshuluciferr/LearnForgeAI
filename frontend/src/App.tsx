import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { SignIn } from "./auth/SignIn";
import { CourseReader } from "./course/CourseReader";
import { Library } from "./library/Library";

function Routed() {
  const { learner, checking } = useAuth();

  if (checking) return <p className="centre muted">…</p>;
  if (!learner) return <SignIn />;

  return (
    <Routes>
      <Route path="/" element={<Library />} />
      {/* Not /courses/:id — that is the API's path, and the same url cannot mean both a JSON
          document and a page. */}
      <Route path="/read/:courseId" element={<CourseReader />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routed />
      </AuthProvider>
    </BrowserRouter>
  );
}
