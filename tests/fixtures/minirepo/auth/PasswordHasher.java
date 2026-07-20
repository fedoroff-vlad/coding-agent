package fixture.auth;

/** Password hashing and verification for user login. */
public class PasswordHasher {

    public String hash(String rawPassword) {
        return Integer.toHexString(rawPassword.hashCode());
    }

    public boolean verify(String rawPassword, String storedHash) {
        return hash(rawPassword).equals(storedHash);
    }
}
