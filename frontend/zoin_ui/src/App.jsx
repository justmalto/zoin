import { useState } from 'react'
import './App.css'
import Login from './pages/login'
import Register from './pages/register'
import Home from './pages/home'
import Layout from './components/layout/layout'
import Profile from './pages/profile'
import {BrowserRouter,Routes,Route,Navigate} from "react-router-dom";

function App() {

  return (
   
    <Routes>
      <Route path='/' element={<Navigate to='/login' replace/>}/>

      <Route path='/login' element={<Login/>}/>
      <Route path='/register' element={<Register/>}/>
      <Route path='/home' element={<Home/>}/>
    </Routes>
    

  )
}

export default App
