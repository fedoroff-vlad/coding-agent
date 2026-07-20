package fixture.math;

/** Vector arithmetic: dot product and cosine similarity. */
public class VectorMath {

    public double dot(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++) {
            sum += a[i] * b[i];
        }
        return sum;
    }

    public double cosine(double[] a, double[] b) {
        return dot(a, b) / (Math.sqrt(dot(a, a)) * Math.sqrt(dot(b, b)));
    }
}
