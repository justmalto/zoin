import React, {useEffect,useState} from "react";
import "./styles/navbar.css"
import setting from "../assets/Setting.webp"
import profile from "../assets/profilep.png"
import { useNavigate } from "react-router-dom";


function Navbar() {
    const navigate = useNavigate();
    return(
        <div className="navbar">
            <div>
                <img src={profile} className="pfp-icon" onClick={()=> navigate("/profile")}/>
            </div>
        </div>
    )
}

export default Navbar;