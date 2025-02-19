"""Flows consisting of jobs to fit ML potentials."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import ase.io
from jobflow import Flow, Maker, job

from autoplex.fitting.common.jobs import machine_learning_fit
from autoplex.fitting.common.regularization import set_sigma
from autoplex.fitting.common.utils import (
    get_list_of_vasp_calc_dirs,
    vaspoutput_2_extended_xyz,
    write_after_distillation_data_split,
)

__all__ = [
    "MLIPFitMaker",
    "DataPreprocessing",
]


@dataclass
class MLIPFitMaker(Maker):
    """
    Maker to fit ML potentials based on DFT labeled reference data.

    This Maker will filter the provided dataset in a data preprocessing step and then proceed
    with the MLIP fit (default is GAP).

    Parameters
    ----------
    name : str
        Name of the flows produced by this maker.
    mlip_type: str
        Choose one specific MLIP type:
        'GAP' | 'J-ACE' | 'P-ACE' | 'NEQUIP' | 'M3GNET' | 'MACE'
    hyper_param_optimization: bool
        Perform hyperparameter optimization using XPOT
        (XPOT: https://pubs.aip.org/aip/jcp/article/159/2/024803/2901815)
    ref_energy_name : str, optional
        Reference energy name.
    ref_force_name : str, optional
        Reference force name.
    ref_virial_name : str, optional
        Reference virial name.
    glue_file_path: str
        Name of the glue.xml file path.
    """

    name: str = "MLpotentialFit"
    mlip_type: str = "GAP"
    hyper_param_optimization: bool = False
    ref_energy_name: str = "REF_energy"
    ref_force_name: str = "REF_forces"
    ref_virial_name: str = "REF_virial"
    glue_file_path: str = "glue.xml"

    # TO DO: Combine parameters used only for gap into one category (as noted below),
    # otherwise it will be too specific.
    def make(
        self,
        fit_input: dict | None = None,  # This is specific to phonon workflow
        species_list: list | None = None,
        isolated_atoms_energies: dict | None = None,
        split_ratio: float = 0.4,
        f_max: float = 40.0,
        regularization: bool = False,  # This is only used for GAP.
        distillation: bool = True,
        separated: bool = False,
        pre_xyz_files: list[str] | None = None,
        pre_database_dir: str | None = None,
        atomwise_regularization_parameter: float = 0.1,  # This is only used for GAP.
        f_min: float = 0.01,  # unit: eV Å-1
        atom_wise_regularization: bool = True,  # This is only used for GAP.
        auto_delta: bool = False,  # This is only used for GAP.
        glue_xml: bool = False,  # This is only used for GAP.
        num_processes_fit: int | None = None,
        preprocessing_data: bool = True,
        database_dir: Path | None = None,
        device: str = "cuda",
        **fit_kwargs,
    ):
        """
        Make a flow to create ML potential fits.

        Parameters
        ----------
        species_list : list.
            List of element names (str)
        isolated_atoms_energies : dict
            Dict of isolated atoms energies.
        fit_input : dict.
            CompletePhononDFTMLDataGenerationFlow output.
        split_ratio: float.
            Parameter to divide the training set and the test set.
            A value of 0.1 means that the ratio of the training set to the test set is 9:1.
        f_max: float
            Maximally allowed force in the data set.
        regularization: bool
            For using sigma regularization.
        distillation: bool
            For using data distillation.
        separated: bool
            Repeat the fit for each data_type available in the (combined) database.
        pre_xyz_files: list[str] or None
            names of the pre-database train xyz file and test xyz file.
        pre_database_dir:
            the pre-database directory.
        atomwise_regularization_parameter: float
            regularization value for the atom-wise force components.
        f_min: float
            minimal force cutoff value for atom-wise regularization.
        atom_wise_regularization: bool
            for including atom-wise regularization.
        auto_delta: bool
            automatically determine delta for 2b, 3b and soap terms.
        glue_xml: bool
            use the glue.xml core potential instead of fitting 2b terms.
        num_processes_fit: int
            number of processes for fitting.
        preprocessing_data: bool
            Determine whether to preprocess the data.
            If not, one needs to input the path to the training database.
        database_dir: Path
            the database directory.
        device: str
            specify device to use cuda or cpu.
        fit_kwargs : dict
            dict including MLIP fit keyword args.
        """
        if database_dir is None:
            database_dir = Path.cwd()

        if self.mlip_type not in ["GAP", "J-ACE", "P-ACE", "NEQUIP", "M3GNET", "MACE"]:
            raise ValueError(
                "Please correct the MLIP name!"
                "The current version ONLY supports the following models: GAP, J-ACE, P-ACE, NEQUIP, M3GNET, and MACE."
            )

        if preprocessing_data:
            jobs = []
            data_prep_job = DataPreprocessing(
                split_ratio=split_ratio,
                regularization=regularization,
                separated=separated,
                distillation=distillation,
                f_max=f_max,
            ).make(
                fit_input=fit_input,
                pre_xyz_files=pre_xyz_files,
                pre_database_dir=pre_database_dir,
                f_min=f_min,
                atomwise_regularization_parameter=atomwise_regularization_parameter,
                atom_wise_regularization=atom_wise_regularization,
            )
            jobs.append(data_prep_job)

            mlip_fit_job = machine_learning_fit(
                database_dir=data_prep_job.output,
                isolated_atoms_energies=isolated_atoms_energies,
                num_processes_fit=num_processes_fit,
                auto_delta=auto_delta,
                glue_xml=glue_xml,
                glue_file_path=self.glue_file_path,
                mlip_type=self.mlip_type,
                hyper_param_optimization=self.hyper_param_optimization,
                ref_energy_name=self.ref_energy_name,
                ref_force_name=self.ref_force_name,
                ref_virial_name=self.ref_virial_name,
                device=device,
                species_list=species_list,
                **fit_kwargs,
            )
            jobs.append(mlip_fit_job)

            return Flow(jobs=jobs, output=mlip_fit_job.output, name=self.name)
        # this will only run if train.extxyz and test.extxyz files are present in the database_dir
        mlip_fit_job = machine_learning_fit(
            database_dir=database_dir,
            isolated_atoms_energies=isolated_atoms_energies,
            num_processes_fit=num_processes_fit,
            auto_delta=auto_delta,
            glue_xml=glue_xml,
            glue_file_path=self.glue_file_path,
            mlip_type=self.mlip_type,
            hyper_param_optimization=self.hyper_param_optimization,
            ref_energy_name=self.ref_energy_name,
            ref_force_name=self.ref_force_name,
            ref_virial_name=self.ref_virial_name,
            device=device,
            species_list=species_list,
            **fit_kwargs,
        )

        return Flow(jobs=mlip_fit_job, output=mlip_fit_job.output, name=self.name)


@dataclass
class DataPreprocessing(Maker):
    """
    Data preprocessing of the provided dataset.

    Parameters
    ----------
    name : str
        Name of the flows produced by this maker.
    split_ratio: float
        Parameter to divide the training set and the test set.
        A value of 0.1 means that the ratio of the training set to the test set is 9:1
    regularization: bool
        For using sigma regularization.
    separated: bool
        Repeat the fit for each data_type available in the (combined) database.
    distillation: bool
        For using data distillation.
    f_max: float
        Maximally allowed force in the data set.

    """

    name: str = "data_preprocessing_for_fitting"
    split_ratio: float = 0.5
    regularization: bool = False
    separated: bool = False
    distillation: bool = False
    f_max: float = 40.0

    @job
    def make(
        self,
        fit_input: dict,
        pre_database_dir: str | None = None,
        pre_xyz_files: list[str] | None = None,
        atomwise_regularization_parameter: float = 0.1,
        f_min: float = 0.01,  # unit: eV Å-1
        atom_wise_regularization: bool = True,
    ):
        """
        Maker for data preprocessing.

        Parameters
        ----------
        fit_input:
            Mixed list of dictionary and lists of the fit input data.
        pre_database_dir: str or None
            the pre-database directory.
        pre_xyz_files: list[str] or None
            names of the pre-database train xyz file and test xyz file labeled by VASP.
        atomwise_regularization_parameter: float
            regularization value for the atom-wise force components.
        f_min: float
            minimal force cutoff value for atom-wise regularization.
        atom_wise_regularization: bool
            for including atom-wise regularization.

        """
        if pre_xyz_files is None:
            pre_xyz_files = ["train.extxyz", "test.extxyz"]

        list_of_vasp_calc_dirs = get_list_of_vasp_calc_dirs(flow_output=fit_input)

        config_types = [
            key
            for key, value in fit_input.items()
            for key2, value2 in value.items()
            if key2 != "phonon_data"
            for _ in value2[0]
        ]

        data_types = [
            key2
            for key, value in fit_input.items()
            for key2, value2 in value.items()
            if key2 != "phonon_data"
            for _ in value2[0]
        ]

        if pre_database_dir and os.path.exists(pre_database_dir):
            current_working_directory = os.getcwd()

            if len(pre_xyz_files) == 1:
                for file_name in pre_xyz_files:
                    source_file_path = os.path.join(pre_database_dir, file_name)
                    destination_file_path = os.path.join(
                        current_working_directory, "vasp_ref.extxyz"
                    )
                    shutil.copy(source_file_path, destination_file_path)
                    print(
                        f"File {file_name} has been copied to {destination_file_path}"
                    )

        vaspoutput_2_extended_xyz(
            path_to_vasp_static_calcs=list_of_vasp_calc_dirs,
            config_types=config_types,
            data_types=data_types,
            f_min=f_min,
            regularization=atomwise_regularization_parameter,
            atom_wise_regularization=atom_wise_regularization,
        )

        write_after_distillation_data_split(
            self.distillation, self.f_max, self.split_ratio
        )

        # Merging database
        if pre_database_dir and os.path.exists(pre_database_dir):
            if len(pre_xyz_files) == 2:
                files_new = ["train.extxyz", "test.extxyz"]
                for file_name, file_new in zip(pre_xyz_files, files_new):
                    with (
                        open(os.path.join(pre_database_dir, file_name)) as pre_xyz_file,
                        open(file_new, "a") as xyz_file,
                    ):
                        xyz_file.write(pre_xyz_file.read())
                    print(f"File {file_name} has been copied to {file_new}")

            elif len(pre_xyz_files) > 2:
                raise ValueError(
                    "Please provide a train and a test extxyz file (two files in total) for the pre_xyz_files."
                )
        if self.regularization:
            atoms = ase.io.read("train.extxyz", index=":")
            ase.io.write("train_wo_sigma.extxyz", atoms, format="extxyz")
            atoms_with_sigma = set_sigma(
                atoms,
                reg_minmax=[(0.1, 1), (0.001, 0.1), (0.0316, 0.316), (0.0632, 0.632)],
            )
            ase.io.write("train.extxyz", atoms_with_sigma, format="extxyz")
        if self.separated:
            atoms_train = ase.io.read("train.extxyz", index=":")
            atoms_test = ase.io.read("test.extxyz", index=":")
            for dt in set(data_types):
                data_type = dt.rstrip("_dir")
                if data_type != "iso_atoms":
                    for atoms in atoms_train + atoms_test:
                        if atoms.info["data_type"] == "iso_atoms":
                            ase.io.write(
                                f"vasp_ref_{data_type}.extxyz",
                                atoms,
                                format="extxyz",
                                append=True,
                            )
                        if atoms.info["data_type"] == data_type:
                            ase.io.write(
                                f"vasp_ref_{data_type}.extxyz",
                                atoms,
                                format="extxyz",
                                append=True,
                            )

                    write_after_distillation_data_split(
                        distillation=self.distillation,
                        f_max=self.f_max,
                        split_ratio=self.split_ratio,
                        vasp_ref_name=f"vasp_ref_{data_type}.extxyz",
                        train_name=f"train_{data_type}.extxyz",
                        test_name=f"test_{data_type}.extxyz",
                    )

        return Path.cwd()
