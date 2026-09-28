import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ReaderApp } from "./ReaderApp";
import "./reader.css";

createRoot(document.getElementById("root")!).render(<StrictMode><ReaderApp /></StrictMode>);
