import React,{useState,useEffect} from "react";
import Navbar from "../components/navbar";
import JobCard from "../components/jobCard";
import "./styles/home.css"

function Home() {


    return(
        <div className="home-container">
            <div><Navbar/></div>
            <div className="card-container"><JobCard/></div>
            
        </div>
    )
}

export default Home;