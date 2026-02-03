import { useState } from "react";
import "./styles/register.css";
import { useNavigate } from "react-router-dom";

function Register() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const navigate = useNavigate();

  const handleSubmit = (e) => {
    e.preventDefault();

    if (password !== confirmPassword) {
      alert("Passwords do not match");
      return;
    }

    const payload = {
      username,
      email,
      password
    };

    console.log(payload);
  };

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <h2 className="login-font">REGISTER</h2>

      <div className="form">

        <input
        type ="name"
        placeholder="Name"
        value={name}
        onChange={(e)=> setName(e.target.value)}
        />

        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <input
          type="password"
          placeholder="Confirm Password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
        />
      </div>

      <button type="submit">sign up</button>
      <p>
        Already have an account? <span onClick={() => navigate("/login")}>LOGIN</span>
      </p>
    </form>
  );
}

export default Register;
