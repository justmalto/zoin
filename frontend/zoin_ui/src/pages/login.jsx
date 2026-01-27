import React, {useState, useEffect} from "react";
import "./styles/login.css"
import { useNavigate } from "react-router-dom";

function Login() {
    const [identifier, setIdentifier] =useState("")
    const [password,setPassword] = useState("")
    const navigate = useNavigate();

    const handleSubmit = (e) => {
        e.preventDefault()

        const payload ={
            identifier,
            password
        }

        console.log(payload)
    }

    return(
        <form className="auth-form" onSubmit={handleSubmit}>
            <h2 className="login-font">
                LOGIN
            </h2>
            <div className="form">
            <input
            placeholder="email or username" className="form-identifier"
            value={identifier}
            onChange={(e)=>setIdentifier(e.target.value)}
            />

            <input type="password" className="form-pass"
            placeholder="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            />

            </div>
            <button type="submit">sign in</button>

            <p>
                Don't have an account? <span onClick={() => { navigate("/register");
                    console.log("Register Clicked");
                 }}>
                REGISTER</span>
            </p>


        </form>
    )



}

export default Login;