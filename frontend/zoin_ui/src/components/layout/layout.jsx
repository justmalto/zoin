import "./layout.css";

function Layout({ children }) {
  return (
    <div className="app-layout">
        <div className="layout-content">
            {children}
        </div>
    </div>
  );
}

export default Layout;
