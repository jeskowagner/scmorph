import re
from typing import Protocol
from warnings import warn

import numpy as np
import pandas as pd
from anndata import AnnData

from scmorph.io import make_AnnData


# Helper functions and classes for typing
class _Classifier(Protocol):
    """Classifier is a generic class to describe the type of a classifier."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.fit(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict(X)


def _is_label_binary(labels: np.ndarray | pd.Series) -> bool:
    """
    Check if labels are binary

    Parameters
    ----------
    labels : ndarray or pd.Series
            Vector of labels

    Returns
    -------
    adata : :class:`~bool`
            True if labels are binary
    """
    return len(set(labels)) == 2


def _default_qc_classifiers(binary: bool = True) -> _Classifier:
    """
    Default classifiers for image-based QC

    Parameters
    ----------
    binary : bool
            Is the classification a binary problem?

    Returns
    -------
    classifier : :class:`~Classifier`
        LDA in binary case, MultiTaskLasso in multiclass case.
    """
    if binary:
        from sklearn.discriminant_analysis import LinearDiscriminantAnalysis

        classifier = LinearDiscriminantAnalysis()
    else:
        from sklearn.linear_model import MultiTaskLasso

        classifier = MultiTaskLasso()
    return classifier


def _prob_to_pred(pred: np.ndarray, decision_boundary: float = 0.5) -> np.ndarray:
    """
    Threshold predictions to binary labels

    Parameters
    ----------
    pred : np.array
        Array of predicted labels

    decision_boundary : float
        Decision boundary for binary classification

    Returns
    -------
    adata : :class:`~numpy.ndarray`
        Array of binary labels
    """
    # Check if predictions are strings, in which case just return them
    if not np.issubdtype(pred.dtype, np.number):
        return pred.dtype
    if len(pred.shape) > 1:
        pred = np.argmax(pred, axis=1)
    else:
        if pred.dtype == int:
            return pred  # model is not regression
        pred = np.where(pred > decision_boundary, 1, 0)
    return pred


# User-facing functions


def read_image_qc(
    filename: str,
    meta_cols: list[str] | None = None,
    label_col: str = "Image_Metadata_QClabel",
    sep: str = ",",
    feature_delim: str = "_",
) -> AnnData:
    """
    Read image metrics from csv file

    Note that you will manually have to add a labeled column into the file.

    Parameters
    ----------
    filename : str
            Path to .csv file

    meta_cols : list
            Names of metadata columns. None for automatic detection. Default: None

    label_col : str
            Column name of column containing labels

    sep : str
            Column deliminator

    feature_delim : str
            Character delimiting feature names

    Returns
    -------
    adata : :class:`~anndata.AnnData`
    """
    df = pd.read_csv(filename, sep=sep)
    labels = df.pop(label_col)

    if meta_cols is None:
        re_meta = re.compile("Metadata", re.IGNORECASE)
        meta_cols = df.filter(regex=re_meta).columns.to_list()

    qc = make_AnnData(df, meta_cols, feature_delim=feature_delim)
    qc.obs["label"] = labels.values
    return qc


def qc_images(
    adata: AnnData,
    qc: AnnData,
    classifier: None | _Classifier = None,
    passing_label: int = 1,
    copy: bool = False,
) -> AnnData:
    """
    Perform cell-QC based on image metrics, if needed using a classifier and a subset of labeled images.

    Parameters
    ----------
    adata :class:`~anndata.AnnData`
            Object as returned by :func:`scmorph.read_cellprofiler`. Represents AnnData object populated with single-cell data.

    qc :class:`~anndata.AnnData`
            Object as returned by :func:`scmorph.read_image_qc`. Represents AnnData object populated with image-QC data.

    classifier : Classifier
            Classifier to use for prediction. If None, will use the LASSO classifier.

    passing_label : int
            Label to use for passing images. Default: 1

    copy : bool
            Return a copy instead of writing to `adata`. Default: False

    Returns
    -------
    adata : :class:`~anndata.AnnData`
    """
    if "label" not in qc.obs.columns:
        raise ValueError("QC data must have a label column")

    # merge QC metadata with model metadata
    qc_full = pd.merge(adata.obs, qc.obs.reset_index(), how="left")
    if qc_full["index"].isna().any():
        raise ValueError("Some wells do not have corresponding QC data." + " Did you import the correct QC data?")

    # extract train data indeces

    if not qc_full["label"].isna().any():
        warn("All wells have complete QC data. No inference will be performed.", stacklevel=1)
        adata.obs["qc_label"] = qc_full["label"]
    else:
        train_ind = qc.obs.loc[~qc.obs["label"].isna()].index
        qc_train = qc[train_ind].copy()
        qc_pred = qc[~qc.obs_names.isin(train_ind)].copy()

        if classifier is None:
            is_prob_binary = _is_label_binary(qc_train.obs["label"])
            classifier = _default_qc_classifiers(is_prob_binary)

        classifier.fit(qc_train.X, qc_train.obs["label"])
        pred = classifier.predict(qc_pred.X)
        pred = _prob_to_pred(pred)

        qc_train.obs["assigned_label"] = qc_train.obs["label"]
        qc_pred.obs["assigned_label"] = pred

        meta_labelled = pd.concat([qc_train.obs, qc_pred.obs])
        # Save QC data to model's obsm slot
        adata.obs["image_qc"] = pd.merge(adata.obs, meta_labelled, how="left")["assigned_label"].values

    if copy:
        return adata[adata.obs["image_qc"] == passing_label, :].copy()

    adata._inplace_subset_obs(adata.obs["image_qc"] == passing_label)
    return adata
