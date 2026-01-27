import Navbar from "../components/navbar";
import "./styles/profile.css"
import pfp from "../assets/profilep.png"

function Profile() {
    usename
    return(
        <div className="profile-container">
            <Navbar/>
            <div className="identifier">
                <div className="left-identifier">
                    <h2 className="username">username</h2>
                    <h2 className="email">email</h2>
                    <h2 className="email-label">email-label</h2>
                    <p>email label is needed for the automated update.</p>

                </div>
                <div className="right-identifier">
                    <img src={pfp} className="pfp"/>
                    <p>edit</p>
                </div>
                
            </div>
        </div>
    )
}

export default Profile;