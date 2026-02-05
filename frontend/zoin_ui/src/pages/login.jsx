import React, {useState, useEffect} from "react";
import "./styles/login.css"
import { useNavigate } from "react-router-dom";

function Login() {
    const [email, setEmail] =useState("")
    const [password,setPassword] = useState("")
    const navigate = useNavigate();

    const handleSubmit = async(e) => {
        e.preventDefault()

        try{
            const response = await fetch("http://localhost:8000/auth/login",{
                method:" POST",
                headers: {
                    "Content-Type":"application/json",
                },
                body: JSON.stringify({
                    email,
                    password,
                })
            });

            if (!response.ok){
                throw new Error("Invalid Credentials");
            }

            const data= await response.json

            localStorage.setItem("token", data.access_token );

            navigate("/home")
        }
        catch (error) {
            alert("Login failed. Check Email or Password.")
        }
    }

    return(
        <form className="auth-form" onSubmit={handleSubmit}>
            <h2 className="login-font">
                LOGIN
            </h2>
            <div className="form">
            <input
            placeholder="email" className="form-identifier"
            value={email}
            onChange={(e)=>setEmail(e.target.value)}
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